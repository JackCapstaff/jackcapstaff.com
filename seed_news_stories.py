from datetime import datetime, timedelta
import re

from app import app, db, User, NewsItem


def slugify(text: str) -> str:
    value = text.lower().strip()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return value


def unique_slug(base_title: str) -> str:
    base = slugify(base_title)
    slug = base
    counter = 2
    while NewsItem.query.filter_by(slug=slug).first() is not None:
        slug = f"{base}-{counter}"
        counter += 1
    return slug


def first_excerpt(text: str, max_len: int = 180) -> str:
    compact = " ".join(text.strip().split())
    if len(compact) <= max_len:
        return compact
    return compact[: max_len - 1].rstrip() + "..."


def get_author_id() -> int:
    author = (
        User.query.filter(User.role.in_(["admin", "editor"])).order_by(User.id.asc()).first()
        or User.query.order_by(User.id.asc()).first()
    )
    if author:
        return author.id

    fallback = User(
        username="site-editor",
        email="noreply@jackcapstaff.com",
        name="Site Editor",
        role="admin",
        is_active=True,
    )
    fallback.set_password("change-me-immediately")
    db.session.add(fallback)
    db.session.flush()
    return fallback.id


def seed_news() -> None:
    stories = [
        {
            "title": "Derwent Brass Qualifies for National Finals at the Royal Albert Hall",
            "subtitle": "Historic first qualification for the Derbyshire band after Midlands Championship success.",
            "content": (
                "Derwent Brass has secured qualification for the National Brass Band Championships of Great Britain "
                "National Finals at the Royal Albert Hall following an outstanding performance at the 2026 Midlands "
                "Regional Championships. Under the baton of Musical Director Jack Capstaff, the band achieved third "
                "place in the Championship Section with a performance of Martin Ellerby's Elgar Variations.\n\n"
                "The result marks a historic milestone for the Derbyshire-based band, representing the ensemble's "
                "first qualification for the National Finals at the Royal Albert Hall. Adjudicators praised the "
                "performance for its musicality and control, with Dr Robert Childs describing it as \"a super "
                "musical performance\".\n\n"
                "Speaking after the contest, Jack commented:\n\n"
                "\"I'm incredibly proud of the performance and what the band has achieved. It reflects a huge amount "
                "of hard work and commitment from the players.\"\n\n"
                "The qualification continues a period of sustained artistic growth for the band under Jack's "
                "leadership."
            ),
        },
        {
            "title": "ConsTest Birmingham Open Victory for Derwent Brass",
            "subtitle": "First prize for Derwent Brass in Birmingham under Jack Capstaff.",
            "content": (
                "Derwent Brass continued its impressive contesting success by winning the 2026 ConsTest Birmingham "
                "Open. Conducted by Jack Capstaff, the band delivered a confident and compelling performance of "
                "Philip Sparke's Music of the Spheres to claim first prize against strong opposition.\n\n"
                "The victory follows a series of strong contest results for the band, including qualification for "
                "the National Finals and previous success at the Leicestershire Brass Band Association Contest. "
                "Speaking after the win, Jack described the result as:\n\n"
                "\"A springboard towards the Spring Festival and a place in the Grand Shield - and potentially, in "
                "the years ahead, the British Open.\"\n\n"
                "The result further underlines Derwent Brass's growing reputation as one of the leading progressive "
                "brass bands in the Midlands."
            ),
        },
        {
            "title": "Derby Concert Orchestra Announces 2026 Season",
            "subtitle": "Major repertoire and community performances headline an exciting new season.",
            "content": (
                "Derby Concert Orchestra has announced an exciting programme of concerts for the 2026 season under "
                "Principal Conductor Jack Capstaff. The orchestra's forthcoming concerts will feature major "
                "orchestral repertoire alongside audience favourites and community-focused performances.\n\n"
                "Highlights include the Spring Concert in Wirksworth and the orchestra's annual Summer Proms concert "
                "at Derby Cathedral. The Proms programme will feature works by Brahms, Walton, Holst, Elgar and "
                "Parry, celebrating the great British orchestral tradition.\n\n"
                "The orchestra continues to build its reputation for accessible, high-quality performances across "
                "Derbyshire and the surrounding region."
            ),
        },
        {
            "title": "Continued Contest Success for Derwent Brass",
            "subtitle": "A sustained run of podium finishes and musical progress.",
            "content": (
                "Derwent Brass has continued a remarkable run of contest performances under Musical Director Jack "
                "Capstaff, including victory at the Leicestershire Brass Band Association Contest and further podium "
                "finishes in Championship Section competition.\n\n"
                "The band's performances have been recognised for their musical detail, preparation and artistic "
                "maturity. Following the Leicester victory, the band publicly thanked Jack for his \"musical "
                "leadership, detailed preparation and clear artistic direction\".\n\n"
                "These results continue to strengthen the band's standing nationally while reflecting a sustained "
                "period of musical progress."
            ),
        },
        {
            "title": "Expanding Adjudicating Portfolio",
            "subtitle": "New adjudicating engagements across UK festivals and competitions.",
            "content": (
                "Alongside conducting work, Jack Capstaff continues to expand his adjudicating portfolio through his "
                "involvement with the Association of Brass Band Adjudicators (AoBBA). Recent engagements have "
                "included adjudicating at Unibrass and supporting brass band competitions and festivals around the UK.\n\n"
                "Jack's adjudicating work reflects his ongoing commitment to supporting music-making and encouraging "
                "the next generation of performers and ensembles."
            ),
        },
    ]

    with app.app_context():
        author_id = get_author_id()

        inserted = 0
        skipped = 0
        now = datetime.utcnow()

        for idx, story in enumerate(stories):
            existing = NewsItem.query.filter_by(title=story["title"]).first()
            if existing:
                skipped += 1
                continue

            item = NewsItem(
                title=story["title"],
                slug=unique_slug(story["title"]),
                subtitle=story["subtitle"],
                content=story["content"],
                excerpt=first_excerpt(story["content"]),
                published=True,
                published_at=now - timedelta(minutes=idx),
                author_id=author_id,
            )
            db.session.add(item)
            inserted += 1

        db.session.commit()

    print(f"Inserted: {inserted}, Skipped existing: {skipped}")


if __name__ == "__main__":
    seed_news()
