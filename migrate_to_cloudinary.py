#!/usr/bin/env python
"""
Migrate existing images from local storage to Cloudinary
Finds all image references in the database and uploads them
"""

import os
import sys
from pathlib import Path
from app import app, db, NewsItem, Event, PageContent

try:
    import cloudinary
    import cloudinary.uploader
except Exception:
    print("❌ cloudinary package not installed")
    sys.exit(1)


def get_local_image_path(image_ref):
    """Resolve image reference to local filesystem path"""
    image_ref = (image_ref or '').strip()
    if not image_ref:
        return None
    
    # Remove leading slashes and normalize
    image_ref = image_ref.lstrip('/')
    
    # Check /assets/uploads/
    uploads_path = Path(app.root_path) / 'assets' / 'uploads' / image_ref.replace('assets/uploads/', '')
    if uploads_path.exists():
        return uploads_path
    
    # Check /images/
    images_path = Path(app.root_path) / 'images' / image_ref.replace('images/', '')
    if images_path.exists():
        return images_path
    
    return None


def upload_to_cloudinary(file_path, prefix='content'):
    """Upload image file to Cloudinary"""
    try:
        with open(file_path, 'rb') as f:
            result = cloudinary.uploader.upload(
                f,
                folder=f'jackcapstaff/{prefix}',
                resource_type='image',
                use_filename=True,
                unique_filename=False,
            )
        return result.get('secure_url')
    except Exception as e:
        print(f"   ❌ Upload failed: {e}")
        return None


def migrate_images():
    """Migrate all images from database to Cloudinary"""
    with app.app_context():
        print("=" * 80)
        print("MIGRATE IMAGES TO CLOUDINARY")
        print("=" * 80)
        
        # Check Cloudinary config
        if not os.environ.get('CLOUDINARY_URL', '').strip():
            print("\n❌ CLOUDINARY_URL env var not set")
            sys.exit(1)
        
        cloudinary.config(secure=True)
        print(f"\n✅ Cloudinary configured (cloud: {cloudinary.config().cloud_name})")
        
        migrated_count = 0
        skipped_count = 0
        
        # Migrate NewsItem featured images
        print("\n📰 Migrating NewsItem featured images...")
        news_items = NewsItem.query.filter(NewsItem.featured_image.isnot(None)).all()
        for item in news_items:
            if not item.featured_image or item.featured_image.startswith('https://'):
                skipped_count += 1
                continue
            
            file_path = get_local_image_path(item.featured_image)
            if not file_path:
                print(f"   ⚠️  News #{item.id} image not found: {item.featured_image}")
                skipped_count += 1
                continue
            
            cloudinary_url = upload_to_cloudinary(file_path, prefix='news')
            if cloudinary_url:
                item.featured_image = cloudinary_url
                db.session.commit()
                print(f"   ✓ News #{item.id}: {file_path.name}")
                migrated_count += 1
            else:
                skipped_count += 1
        
        # Migrate Event featured images
        print("\n🎭 Migrating Event featured images...")
        events = Event.query.filter(Event.featured_image.isnot(None)).all()
        for item in events:
            if not item.featured_image or item.featured_image.startswith('https://'):
                skipped_count += 1
                continue
            
            file_path = get_local_image_path(item.featured_image)
            if not file_path:
                print(f"   ⚠️  Event #{item.id} image not found: {item.featured_image}")
                skipped_count += 1
                continue
            
            cloudinary_url = upload_to_cloudinary(file_path, prefix='events')
            if cloudinary_url:
                item.featured_image = cloudinary_url
                db.session.commit()
                print(f"   ✓ Event #{item.id}: {file_path.name}")
                migrated_count += 1
            else:
                skipped_count += 1
        
        # Migrate PageContent images (look in content field for image URLs)
        print("\n📄 Migrating PageContent images...")
        pages = PageContent.query.all()
        for page in pages:
            if not page.content or page.content.startswith('https://'):
                continue
            
            # Simple regex to find image URLs in content
            import re
            img_pattern = r'(?:src|href)=["\']([^"\']+\.(jpg|jpeg|png|gif|webp))["\']'
            matches = re.findall(img_pattern, page.content, re.IGNORECASE)
            
            if not matches:
                continue
            
            updated_content = page.content
            for img_url, ext in matches:
                if img_url.startswith('https://'):
                    continue
                
                file_path = get_local_image_path(img_url)
                if not file_path:
                    print(f"   ⚠️  Page #{page.id} image not found: {img_url}")
                    continue
                
                cloudinary_url = upload_to_cloudinary(file_path, prefix='pages')
                if cloudinary_url:
                    updated_content = updated_content.replace(img_url, cloudinary_url)
                    print(f"   ✓ Page #{page.id}: {file_path.name}")
                    migrated_count += 1
                else:
                    skipped_count += 1
            
            if updated_content != page.content:
                page.content = updated_content
                db.session.commit()
        
        print("\n" + "=" * 80)
        print(f"✅ MIGRATION COMPLETE")
        print(f"   Migrated: {migrated_count} images")
        print(f"   Skipped:  {skipped_count} images")
        print("=" * 80)


if __name__ == '__main__':
    migrate_images()
