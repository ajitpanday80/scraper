// WhatsApp bridge: runs whatsapp-web.js and exposes a tiny localhost-only HTTP API to the Flask app.
//   GET  /status   -> {state, qr, number, name}
//   POST /send     -> {phone, text, media:[{path, kind}]}
//   GET  /inbox    -> incoming 1:1 messages since the last call (drained)
//   POST /logout   -> unlink the current number, wipe its session, start a fresh QR (this is "switch number")
// Every request needs the X-Token header to match WA_BRIDGE_TOKEN.
const http = require('http');
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const QRCode = require('qrcode');
const { Client, LocalAuth, MessageMedia } = require('whatsapp-web.js');

const PORT = parseInt(process.env.WA_BRIDGE_PORT || '3399', 10);
const TOKEN = process.env.WA_BRIDGE_TOKEN || '';
const SESSION_DIR = path.resolve(process.env.WA_SESSION_DIR || path.join(__dirname, 'session'));
const UPLOAD_DIR = path.resolve(process.env.UPLOAD_DIR || path.join(__dirname, '..', 'uploads'));
if (!TOKEN) { console.error('WA_BRIDGE_TOKEN is not set'); process.exit(1); }

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const rand = (a, b) => a + Math.random() * (b - a);

// whatsapp-web.js defaults to a Chrome 101 / macOS user agent, which WhatsApp now treats as an outdated browser and
// refuses to link. Report the Chrome that actually runs here (real major version, real platform).
const CHROME_MAJOR = (require('puppeteer').executablePath().match(/(\d+)\.\d+\.\d+\.\d+/) || [, '146'])[1];
const USER_AGENT = process.env.WA_USER_AGENT ||
  `Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/${CHROME_MAJOR}.0.0.0 Safari/537.36`;

let client = null;
let st = { state: 'starting', qr: '', number: '', name: '' };
let busy = false; // one send at a time
let inbox = []; // incoming replies, drained by the Flask app (capped so it can't grow forever)

function start() {
  st = { state: 'starting', qr: '', number: '', name: '' };
  client = new Client({
    authStrategy: new LocalAuth({ dataPath: SESSION_DIR }),
    userAgent: USER_AGENT,
    puppeteer: { headless: true, args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage', '--disable-gpu'] },
  });
  const me = client;
  me.on('qr', async (qr) => { if (me === client) st = { ...st, state: 'qr', qr: await QRCode.toDataURL(qr, { margin: 1, width: 320 }) }; });
  me.on('ready', () => {
    if (me !== client) return;
    st = { state: 'ready', qr: '', number: me.info.wid.user, name: me.info.pushname || '' };
    console.log('ready as', st.number);
  });
  me.on('message', async (msg) => {
    if (me !== client || msg.fromMe || msg.isStatus || String(msg.from).endsWith('@g.us')) return;
    try {
      const number = String(msg.from).endsWith('@c.us') ? String(msg.from).split('@')[0] : (await msg.getContact()).number;
      inbox.push({ from: String(number || '').replace(/\D/g, ''), body: String(msg.body || (msg.hasMedia ? '[attachment]' : '')).slice(0, 500), ts: msg.timestamp });
      if (inbox.length > 500) inbox = inbox.slice(-500);
    } catch (e) { console.error('inbox:', e.message); }
  });
  me.on('auth_failure', (m) => { console.error('auth_failure', m); if (me === client) st = { ...st, state: 'disconnected', qr: '' }; });
  me.on('disconnected', (why) => {
    console.error('disconnected', why);
    if (me !== client) return;
    st = { state: 'disconnected', qr: '', number: '', name: '' };
    setTimeout(() => { if (me === client) restart(false); }, 5000);
  });
  me.initialize().catch((e) => { console.error('initialize failed', e.message); if (me === client) st = { ...st, state: 'disconnected' }; });
}

async function restart(wipe) {
  const old = client;
  if (wipe) inbox = [];
  client = null;
  st = { state: 'starting', qr: '', number: '', name: '' };
  if (old) {
    if (wipe) { try { await old.logout(); } catch (e) { console.error('logout:', e.message); } }
    try { await old.destroy(); } catch (e) { /* browser may already be gone */ }
  }
  if (wipe) fs.rmSync(SESSION_DIR, { recursive: true, force: true });
  start();
}

// WhatsApp Web renamed id._serialized to id.$1 in July 2026; whatsapp-web.js 1.34.7 predates that, so read either.
const serial = (id) => (id && (id._serialized || id.$1)) || '';

async function send({ phone, text, media }) {
  if (st.state !== 'ready') return { ok: false, error: 'WhatsApp is not connected' };
  const digits = String(phone || '').replace(/\D/g, '');
  if (digits.length < 11 || digits.length > 15) return { ok: false, error: 'invalid phone number', lead: true };
  const wid = await client.getNumberId(digits);
  if (!wid) return { ok: false, error: 'number is not on WhatsApp', lead: true };
  const chatId = serial(wid);
  if (!chatId) return { ok: false, error: 'could not resolve WhatsApp chat id' };
  try { const chat = await client.getChatById(chatId); await chat.sendStateTyping(); await sleep(rand(2000, 5000)); await chat.clearState(); } catch (e) { /* cosmetic only */ }
  let last;
  const files = (media || []).map((m) => path.resolve(String(m.path || '')))
    .filter((p) => p.startsWith(UPLOAD_DIR + path.sep) && fs.existsSync(p));
  if (files.length) {
    for (let i = 0; i < files.length; i++) {
      const mm = MessageMedia.fromFilePath(files[i]);
      last = await client.sendMessage(chatId, mm, i === 0 && text ? { caption: text } : {});
      if (i < files.length - 1) await sleep(rand(1500, 3500));
    }
  } else {
    last = await client.sendMessage(chatId, text);
  }
  if (!last) return { ok: false, error: 'WhatsApp did not accept the message' };
  return { ok: true, id: serial(last.id) || '' };
}

const json = (res, code, body) => { res.writeHead(code, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(body)); };
const readBody = (req) => new Promise((resolve, reject) => {
  let b = ''; req.on('data', (c) => { b += c; if (b.length > 1e6) { reject(new Error('too large')); req.destroy(); } });
  req.on('end', () => { try { resolve(b ? JSON.parse(b) : {}); } catch (e) { reject(e); } });
});

http.createServer(async (req, res) => {
  const given = Buffer.from(String(req.headers['x-token'] || ''));
  const want = Buffer.from(TOKEN);
  if (given.length !== want.length || !crypto.timingSafeEqual(given, want)) return json(res, 403, { error: 'forbidden' });
  try {
    if (req.method === 'GET' && req.url === '/status') return json(res, 200, st);
    if (req.method === 'GET' && req.url === '/inbox') { const out = inbox; inbox = []; return json(res, 200, out); }
    if (req.method === 'POST' && req.url === '/send') {
      if (busy) return json(res, 200, { ok: false, error: 'another message is being sent' });
      busy = true;
      try { return json(res, 200, await send(await readBody(req))); } finally { busy = false; }
    }
    if (req.method === 'POST' && req.url === '/logout') { restart(true); return json(res, 200, { ok: true }); }
    json(res, 404, { error: 'not found' });
  } catch (e) {
    console.error('request failed', e);
    json(res, 200, { ok: false, error: `${e.name}: ${e.message}`.slice(0, 200) });
  }
}).listen(PORT, '127.0.0.1', () => console.log('wa-bridge listening on 127.0.0.1:' + PORT));

start();
process.on('SIGTERM', async () => { try { await client.destroy(); } catch (e) { /* exiting anyway */ } process.exit(0); });
