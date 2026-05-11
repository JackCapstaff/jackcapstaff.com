import re

with open('assets/css/main.css', 'r') as f:
    content = f.read()

# Get the full #header { } rule more carefully
print("=== FULL #header CSS RULE (NO ABBREVIATIONS) ===\n")
header_match = re.search(r'#header\s*\{([^}]+)\}', content, re.DOTALL)
if header_match:
    rule_content = header_match.group(1)
    properties = [line.strip() for line in rule_content.split('\n') if line.strip() and ':' in line]
    
    for prop in properties:
        print(prop)
        
    # Check specifically for height: 0
    if re.search(r'height\s*:\s*0\s*[!;]', content):
        print("\n⚠️  Found 'height: 0' somewhere in CSS!")
    else:
        print("\n✓ No 'height: 0' found in CSS")

# Check if header has display: none
if re.search(r'#header\s*\{[^}]*display\s*:\s*none', content, re.DOTALL):
    print("⚠️  #header has display: none!")
else:
    print("✓ #header does NOT have display: none")

# Look for any rules targeting the page wrapper or header container
print("\n=== CHECKING page-wrapper ===\n")
wrapper_match = re.search(r'#page-wrapper\s*\{([^}]+)\}', content, re.DOTALL)
if wrapper_match:
    print(wrapper_match.group(1)[:300])
