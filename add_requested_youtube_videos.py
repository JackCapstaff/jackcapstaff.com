import re
from urllib.parse import quote

import requests

from app import app, db, User, PageContent


REQUESTED_VIDEOS = [
    {
        "display_title": "The Snow Queen",
        "query": "The Snow Queen Jack Capstaff",
        "required_terms": ["snow", "queen"],
        "playlist": "Christmas Concerts",
    },
    {
        "display_title": "Derby Concert Orchestra - Christmas Concert",
        "query": "Derby Concert Orchestra Christmas Concert Jack Capstaff",
        "required_terms": ["derby", "concert", "orchestra", "christmas"],
        "playlist": "Christmas Concerts",
    },
]


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


def extract_video_ids_from_search(search_html: str) -> list[str]:
    ids: list[str] = []
    for part in search_html.split('"videoId":"')[1:]:
        candidate = part[:11]
        if re.fullmatch(r"[A-Za-z0-9_-]{11}", candidate) and candidate not in ids:
            ids.append(candidate)
    return ids


def fetch_oembed(video_id: str) -> dict | None:
    watch_url = f"https://www.youtube.com/watch?v={video_id}"
    oembed_url = f"https://www.youtube.com/oembed?url={quote(watch_url, safe=':/?=&')}&format=json"
    response = requests.get(oembed_url, timeout=20)
    if response.status_code != 200:
        return None
    return response.json()


def find_video_for_request(item: dict) -> tuple[str | None, str | None]:
    search_url = f"https://www.youtube.com/results?search_query={quote(item['query'])}"
    html = requests.get(search_url, timeout=30, headers={"User-Agent": "Mozilla/5.0"}).text
    candidate_ids = extract_video_ids_from_search(html)[:30]

    best_author_match: tuple[str, str] | None = None

    for video_id in candidate_ids:
        meta = fetch_oembed(video_id)
        if not meta:
            continue

        title = (meta.get("title") or "").strip()
        author = (meta.get("author_name") or "").strip()
        title_l = title.lower()
        author_l = author.lower()

        if "jack capstaff" not in author_l:
            continue

        if all(term in title_l for term in item["required_terms"]):
            return video_id, title

        if best_author_match is None:
            best_author_match = (video_id, title)

    if best_author_match:
        return best_author_match

    return None, None


def add_requested_videos() -> None:
    with app.app_context():
        author_id = get_author_id()
        max_order = db.session.query(db.func.max(PageContent.order)).filter_by(page="media").scalar()
        next_order = (max_order or 0) + 1

        inserted = 0
        updated = 0
        missing = []

        for item in REQUESTED_VIDEOS:
            video_id, resolved_title = find_video_for_request(item)

            if not video_id:
                missing.append(item["display_title"])
                continue

            watch_url = f"https://www.youtube.com/watch?v={video_id}"
            section = f"media-youtube-{slugify(video_id)}"
            title = resolved_title or item["display_title"]
            content = f"Playlist: {item['playlist']}\nFeatured performance from Jack Capstaff's YouTube channel."

            existing = (
                PageContent.query.filter_by(page="media", section=section).first()
                or PageContent.query.filter_by(page="media", youtube_embed_url=watch_url).first()
            )

            if existing:
                existing.title = title
                existing.youtube_embed_url = watch_url
                if not (existing.content or "").lower().startswith("playlist:"):
                    existing.content = content
                existing.published = True
                if existing.order is None:
                    existing.order = next_order
                    next_order += 1
                updated += 1
                continue

            block = PageContent(
                page="media",
                section=section,
                title=title,
                content=content,
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

        print(f"Inserted: {inserted}, Updated: {updated}")
        if missing:
            print("Could not find exact videos for:")
            for title in missing:
                print(f"- {title}")


if __name__ == "__main__":
    add_requested_videos()
