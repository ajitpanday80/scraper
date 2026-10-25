"""Google Places API (New) Text Search client."""
import requests

URL = "https://places.googleapis.com/v1/places:searchText"
FIELDS = ",".join([
    "places.id", "places.displayName", "places.formattedAddress",
    "places.nationalPhoneNumber", "places.internationalPhoneNumber",
    "places.rating", "places.userRatingCount", "places.googleMapsUri",
    "nextPageToken",
])


class PlacesError(Exception):
    pass


def search(query, api_key, max_pages=3, region="IN"):
    """Return up to max_pages*20 places for a text query (Google caps this near 60)."""
    out, token = [], None
    for _ in range(max_pages):
        body = {"textQuery": query, "pageSize": 20, "regionCode": region}
        if token:
            body["pageToken"] = token
        r = requests.post(
            URL, json=body, timeout=30,
            headers={"X-Goog-Api-Key": api_key, "X-Goog-FieldMask": FIELDS},
        )
        data = r.json()
        if r.status_code != 200:
            raise PlacesError(data.get("error", {}).get("message", r.text))
        for p in data.get("places", []):
            out.append({
                "place_id": p["id"],
                "name": p.get("displayName", {}).get("text", ""),
                "phone": p.get("nationalPhoneNumber", ""),
                "intl_phone": p.get("internationalPhoneNumber", ""),
                "address": p.get("formattedAddress", ""),
                "rating": p.get("rating"),
                "rating_count": p.get("userRatingCount"),
                "maps_url": p.get("googleMapsUri", ""),
            })
        token = data.get("nextPageToken")
        if not token:
            break
    return out
