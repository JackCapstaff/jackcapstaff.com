import requests
import re

# Check main page
r = requests.get('https://www.jackcapstaff.com/', timeout=20)
cloudinary_urls = re.findall(r'https://res\.cloudinary\.com/[^\s"\'()]+', r.text)

print('📊 CLOUDINARY MIGRATION VERIFICATION')
print('=' * 70)
print(f'✅ Homepage Status: {r.status_code}')
print(f'✅ Cloudinary URLs found: {len(set(cloudinary_urls))} unique')
print()
print('Sample Cloudinary URLs:')
for url in sorted(set(cloudinary_urls))[:5]:
    print(f'  • {url}')
