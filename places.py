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


def search(query, api_key, max_pages=3, region="IN", on_request=None):
    """Return up to max_pages*20 places for a text query (Google caps this near 60).

    on_request is called before every HTTP request (used for usage limits); it may raise PlacesError.
    """
    out, token = [], None
    for _ in range(max_pages):
        if on_request:
            on_request()
        body = {"textQuery": query, "pageSize": 20, "regionCode": region}
        if token:
            body["pageToken"] = token
        try:
            r = requests.post(
                URL, json=body, timeout=30,
                headers={"X-Goog-Api-Key": api_key, "X-Goog-FieldMask": FIELDS},
            )
            data = r.json()
        except (requests.RequestException, ValueError) as e:
            raise PlacesError(f"Could not reach Google: {e}")
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
