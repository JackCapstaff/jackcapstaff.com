#!/usr/bin/env python3
"""
Compress oversized images for Cloudinary upload (>10MB free tier limit).
"""

import os
import sys
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    print("❌ Pillow not installed. Run: pip install pillow")
    sys.exit(1)

PROJECT_ROOT = Path(__file__).parent
IMAGES_DIR = PROJECT_ROOT / 'images'

# Files that exceeded 10MB limit
OVERSIZED_FILES = [
    'Stage-large.jpg',
    'Stage-medium.jpg',
    'Stage-small.jpg',
    'pic03.jpg',
]

def compress_image(file_path, quality=75, max_size=8*1024*1024):
    """Compress image to fit under 10MB with good quality."""
    print(f"  Compressing {file_path.name}...", end="")
    
    try:
        img = Image.open(file_path)
        
        # Convert RGBA to RGB if needed
        if img.mode in ('RGBA', 'LA', 'P'):
            background = Image.new('RGB', img.size, (255, 255, 255))
            background.paste(img, mask=img.split()[-1] if img.mode == 'RGBA' else None)
            img = background
        
        # Save with progressive compression
        current_quality = quality
        while current_quality > 40:
            img.save(file_path, 'JPEG', quality=current_quality, optimize=True)
            file_size = file_path.stat().st_size
            
            if file_size < max_size:
                size_mb = file_size / (1024*1024)
                print(f" ✓ {size_mb:.1f}MB (quality={current_quality})")
                return True
            
            current_quality -= 5
        
        print(f" ⚠️  Could not compress below 10MB (lowest quality=40)")
        return False
    
    except Exception as e:
        print(f" ❌ Error: {e}")
        return False

def main():
    print("=" * 80)
    print("COMPRESS OVERSIZED IMAGES FOR CLOUDINARY")
    print("=" * 80)
    print()
    
    compressed = 0
    for filename in OVERSIZED_FILES:
        file_path = IMAGES_DIR / filename
        if file_path.exists():
            original_size = file_path.stat().st_size / (1024*1024)
            print(f"  {filename} ({original_size:.1f}MB)")
            
            if compress_image(file_path):
                compressed += 1
        else:
            print(f"  ⚠️  {filename} not found")
    
    print()
    print("=" * 80)
    print(f"✅ Compressed {compressed}/{len(OVERSIZED_FILES)} files")
    print("=" * 80)
    print("\n📋 Next: Run `heroku run python migrate_all_images_to_cloudinary.py -a rehearsal-schedule` again")

if __name__ == '__main__':
    main()
