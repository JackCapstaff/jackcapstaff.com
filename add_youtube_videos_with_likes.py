import re
import xml.etree.ElementTree as ET

import requests

from app import app, db, User, PageContent


CHANNEL_ID = "UC61xlb_KrdyOz9jZWJjz87Q"
FEED_URL = f"https://www.youtube.com/feeds/videos.xml?channel_id={CHANNEL_ID}"

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
    "media": "http://search.yahoo.com/mrss/",
}


def slugify(text: str) -> str:
    value = text.lower().strip()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return value


def get_author_id() -> int:
    author = (
        User.query.filter(User.role.in_(["admin", "editor"]))
        .order_by(User.id.asc())
        .first()
    ) or User.query.order_by(User.id.asc()).first()

    if not author:
        raise RuntimeError("No users found; cannot assign author_id for media blocks")

    return author.id


def fetch_videos_with_likes_signal() -> list[dict]:
    response = requests.get(FEED_URL, timeout=30)
    response.raise_for_status()

    root = ET.fromstring(response.text)
    selected = []

    for entry in root.findall("atom:entry", NS):
        video_id = entry.findtext("yt:videoId", default="", namespaces=NS).strip()
        title = entry.findtext("atom:title", default="", namespaces=NS).strip()

        rating = entry.find("media:group/media:community/media:starRating", NS)
        count = int((rating.get("count") if rating is not None else "0") or "0")

        if not video_id or not title or count <= 0:
            continue

        selected.append(
            {
                "video_id": video_id,
                "title": title,
                "rating_count": count,
                "content": f"Featured from Jack Capstaff's YouTube channel (engagement count: {count}).",
            }
        )

    return selected


def add_blocks() -> None:
    videos = fetch_videos_with_likes_signal()

    with app.app_context():
        author_id = get_author_id()

        max_order = db.session.query(db.func.max(PageContent.order)).filter_by(page="media").scalar()
        next_order = (max_order or 0) + 1

        inserted = 0
        updated = 0
        skipped = 0

        for item in videos:
            watch_url = f"https://www.youtube.com/watch?v={item['video_id']}"
            section = f"media-youtube-{slugify(item['video_id'])}"

            existing = (
                PageContent.query.filter_by(page="media", section=section).first()
                or PageContent.query.filter_by(page="media", youtube_embed_url=watch_url).first()
            )

            if existing:
                # Keep existing title/content if already present from previous import.
                if not existing.youtube_embed_url:
                    existing.youtube_embed_url = watch_url
                    updated += 1
                else:
                    skipped += 1
                continue

            block = PageContent(
                page="media",
                section=section,
                title=item["title"],
                content=item["content"],
                youtube_embed_url=watch_url,
                image_url=None,
                order=next_order,
                published=True,
                author_id=author_id,
            )
            next_order += 1
            db.session.add(block)
            inserted += 1

        db.session.commit()

    print(f"Found videos with likes signal: {len(videos)}")
    print(f"Inserted: {inserted}, Updated: {updated}, Skipped existing: {skipped}")


if __name__ == "__main__":
    add_blocks()
