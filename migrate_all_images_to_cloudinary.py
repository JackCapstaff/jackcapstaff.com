#!/usr/bin/env python3
"""
Comprehensive migration script for all images to Cloudinary.
Uploads static assets and updates references in CSS, SASS, templates, and JavaScript.
"""

import os
import re
import sys
from pathlib import Path

try:
    import cloudinary
    import cloudinary.uploader
except ImportError:
    print("❌ cloudinary package not installed. Run: pip install cloudinary")
    sys.exit(1)

# Check for Cloudinary config
cloudinary_url = os.getenv('CLOUDINARY_URL')
if not cloudinary_url:
    print("❌ CLOUDINARY_URL env var not set")
    sys.exit(1)

print("=" * 80)
print("MIGRATE ALL IMAGES TO CLOUDINARY (STATIC ASSETS + REFERENCES)")
print("=" * 80)
print(f"✅ Cloudinary configured (cloud: {cloudinary.config().cloud_name})\n")

# Directory mappings
PROJECT_ROOT = Path(__file__).parent
ASSETS_DIR = PROJECT_ROOT / 'assets'
IMAGES_DIR = PROJECT_ROOT / 'images'
TEMPLATES_DIR = PROJECT_ROOT / 'templates'
CSS_MAIN = ASSETS_DIR / 'css' / 'main.css'
SASS_MAIN = ASSETS_DIR / 'sass' / 'main.scss'
NOSCRIPT_CSS = ASSETS_DIR / 'css' / 'noscript.css'
NOSCRIPT_SASS = ASSETS_DIR / 'sass' / 'noscript.scss'

# Track uploads and updates
uploaded_files = {}  # local_path -> cloudinary_url
files_updated = {}  # file_path -> count of replacements

def upload_to_cloudinary(file_path, prefix='assets'):
    """Upload a single file to Cloudinary."""
    try:
        result = cloudinary.uploader.upload(
            str(file_path),
            folder=f'jackcapstaff/{prefix}',
            use_filename=True,
            overwrite=False
        )
        return result['secure_url']
    except Exception as e:
        print(f"  ⚠️  Error uploading {file_path}: {e}")
        return None

def upload_static_assets():
    """Upload all static image assets to Cloudinary."""
    print("📦 Uploading static assets...\n")
    
    # Find and upload all images
    image_patterns = ['*.jpg', '*.jpeg', '*.png', '*.gif', '*.webp', '*.svg']
    
    # Upload from /images/
    if IMAGES_DIR.exists():
        for pattern in image_patterns:
            for image_file in IMAGES_DIR.glob(pattern):
                url = upload_to_cloudinary(image_file, prefix='images')
                if url:
                    # Store relative path as key for reference updates
                    rel_path = str(image_file.relative_to(PROJECT_ROOT)).replace('\\', '/')
                    uploaded_files[rel_path] = url
                    print(f"  ✓ {rel_path}")
    
    # Upload from /assets/
    if ASSETS_DIR.exists():
        for subdir in ['css/images', 'webfonts']:
            asset_subdir = ASSETS_DIR / subdir
            if asset_subdir.exists():
                for pattern in image_patterns:
                    for image_file in asset_subdir.glob(pattern):
                        url = upload_to_cloudinary(image_file, prefix=f'assets/{subdir}')
                        if url:
                            rel_path = str(image_file.relative_to(PROJECT_ROOT)).replace('\\', '/')
                            uploaded_files[rel_path] = url
                            print(f"  ✓ {rel_path}")
    
    print(f"\n  📊 Uploaded {len(uploaded_files)} assets\n")

def update_file_references(file_path, replacements):
    """Update image references in a file."""
    if not file_path.exists():
        return 0
    
    try:
        content = file_path.read_text(encoding='utf-8')
        original_content = content
        
        for local_ref, cloudinary_url in replacements.items():
            # Handle various reference formats:
            # url('/images/...'), url('../images/...'), /images/..., etc.
            patterns = [
                (f"'{local_ref}'", f"'{cloudinary_url}'"),
                (f'"{local_ref}"', f'"{cloudinary_url}"'),
                (f"({local_ref})", f"({cloudinary_url})"),
                (f"url({local_ref})", f"url({cloudinary_url})"),
                (f"url('{local_ref}')", f"url('{cloudinary_url}')"),
                (f'url("{local_ref}")', f'url("{cloudinary_url}")'),
                # For relative paths (../images/...)
                (f"'../images/{local_ref.split('/')[-1]}'", f"'{cloudinary_url}'"),
                (f'"../images/{local_ref.split("/")[-1]}"', f'"{cloudinary_url}"'),
            ]
            
            count = 0
            for old_pattern, new_pattern in patterns:
                new_count = content.count(old_pattern)
                if new_count > 0:
                    content = content.replace(old_pattern, new_pattern)
                    count += new_count
            
            if count > 0:
                print(f"    • {local_ref}: {count} references")
        
        # Write back if changed
        if content != original_content:
            file_path.write_text(content, encoding='utf-8')
            return 1
        return 0
    
    except Exception as e:
        print(f"    ⚠️  Error updating {file_path}: {e}")
        return 0

def update_references():
    """Update all references to images in source files."""
    print("🔗 Updating image references in source files...\n")
    
    if not uploaded_files:
        print("  ℹ️  No uploads to reference; skipping updates\n")
        return
    
    # CSS files
    css_files = [
        CSS_MAIN,
        NOSCRIPT_CSS,
        ASSETS_DIR / 'css' / 'scrolling.css',
    ]
    
    # SASS files
    sass_files = [
        SASS_MAIN,
        NOSCRIPT_SASS,
    ]
    
    # HTML templates
    html_files = list(TEMPLATES_DIR.glob('*.html')) if TEMPLATES_DIR.exists() else []
    
    # Prepare replacements dict (normalize paths)
    replacements = {}
    for local_path, cloudinary_url in uploaded_files.items():
        # Normalize for matching (both / and \)
        replacements[local_path] = cloudinary_url
        # Also add variant without 'images/' prefix for root-relative paths
        if 'images/' in local_path:
            short_key = local_path.split('images/')[-1]
            replacements[f'/images/{short_key}'] = cloudinary_url
            replacements[f'images/{short_key}'] = cloudinary_url
    
    # Update CSS
    if CSS_MAIN.exists():
        print("  📄 main.css")
        update_file_references(CSS_MAIN, replacements)
    
    if NOSCRIPT_CSS.exists():
        print("  📄 noscript.css")
        update_file_references(NOSCRIPT_CSS, replacements)
    
    # Update SASS
    if SASS_MAIN.exists():
        print("  📄 main.scss")
        update_file_references(SASS_MAIN, replacements)
    
    # Update HTML templates
    for html_file in html_files:
        print(f"  📄 {html_file.name}")
        update_file_references(html_file, replacements)
    
    print()

def main():
    upload_static_assets()
    update_references()
    
    print("=" * 80)
    print("✅ MIGRATION COMPLETE")
    print(f"   Uploaded: {len(uploaded_files)} images")
    print("=" * 80)
    print("\n📋 Next steps:")
    print("   1. Verify image URLs in CSS/templates: check browser console for any 404s")
    print("   2. Test all pages to ensure background images and content images load")
    print("   3. (Optional) Delete /assets/uploads/ and old /assets/css/images/ if not using locally")

if __name__ == '__main__':
    main()
