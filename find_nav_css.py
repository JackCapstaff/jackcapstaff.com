import re

with open('assets/css/main.css', 'r') as f:
    content = f.read()

# Search for patterns that might hide elements
print("=== SEARCHING FOR #nav RULES ===\n")

nav_rules = []
for match in re.finditer(r'#nav[^{]*\{[^}]+\}', content, re.DOTALL):
    rule = match.group(0)
    nav_rules.append(rule)
    # Check if rule contains display, visibility, or height properties
    if any(x in rule.lower() for x in ['display', 'visibility', 'height']):
        print('⚠️  Found nav rule with display/visibility/height:')
        print(rule[:400])
        print('---\n')

print(f'\nTotal #nav CSS rules found: {len(nav_rules)}\n')

# Look for #header nav rules
print("=== SEARCHING FOR #header nav RULES ===\n")
for match in re.finditer(r'#header\s+nav[^{]*\{[^}]+\}', content, re.DOTALL):
    rule = match.group(0)
    if any(x in rule.lower() for x in ['display', 'visibility', 'height']):
        print('⚠️  Found header nav rule with display/visibility/height:')
        print(rule[:400])
        print('---\n')

# Look for media query rules that might hide nav
print("=== SEARCHING IN MEDIA QUERIES ===\n")
media_queries = re.finditer(r'@media[^{]*\{[^}]*#nav[^}]*\}', content, re.DOTALL)
for match in media_queries:
    rule = match.group(0)
    if 'display' in rule.lower():
        print('Found in media query:')
        print(rule[:300])
        print('---\n')
