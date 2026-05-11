import re

with open('assets/css/main.css', 'r') as f:
    content = f.read()

# Get the full #header { } rule
print("=== SEARCHING FOR #header { } RULE ===\n")
header_match = re.search(r'#header\s*\{[^}]+\}', content, re.DOTALL)
if header_match:
    rule = header_match.group(0)
    print(rule[:800])
    print('\n[... more ...]\n')
    
    # Check for problematic properties
    if 'display' in rule and 'none' in rule:
        print("⚠️  #header has display: none - THIS HIDES THE HEADER!")
    if 'height' in rule.lower():
        if '0' in rule:
            print("⚠️  #header has height: 0 - THIS HIDES THE HEADER!")
        else:
            # Extract the height value
            h_match = re.search(r'height\s*:\s*([^;]+);', rule)
            if h_match:
                print(f"Header height: {h_match.group(1)}")
    if 'visibility' in rule and 'hidden' in rule:
        print("⚠️  #header has visibility: hidden")

# Check #header.alt
print("\n\n=== #header.alt CSS RULES ===\n")
alt_matches = list(re.finditer(r'#header\.alt[^{]*\{[^}]+\}', content, re.DOTALL))
for i, match in enumerate(alt_matches):
    print(f"Rule {i+1}:")
    print(match.group(0))
    print()
