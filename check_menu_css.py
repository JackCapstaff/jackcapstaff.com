import re

with open('assets/css/main.css', 'r') as f:
    content = f.read()

# Look for #menu CSS rules
print("=== SEARCHING FOR #menu CSS RULES ===\n")

menu_matches = list(re.finditer(r'#menu[^{]*\{([^}]+)\}', content, re.DOTALL))
if menu_matches:
    for i, match in enumerate(menu_matches):
        print(f"Rule {i+1}:")
        props = match.group(1).strip().split('\n')
        for prop in props:
            if prop.strip():
                print(f"  {prop.strip()}")
        print()
else:
    print("No #menu CSS rules found\n")

# Check if there's a default hidden state
if re.search(r'#menu.*display\s*:\s*none', content, re.DOTALL):
    print("⚠️  #menu is hidden by default (display: none)")
else:
    print("✓ #menu is NOT hidden by display: none")

if re.search(r'#menu.*visibility\s*:\s*hidden', content, re.DOTALL):
    print("⚠️  #menu is hidden by default (visibility: hidden)")
else:
    print("✓ #menu is NOT hidden by visibility: hidden")

# Look for #header nav #menu rules
print("\n=== LOOKING FOR #header nav #menu ===\n")
nav_menu = re.search(r'#header\s+nav\s+#menu[^{]*\{([^}]+)\}', content, re.DOTALL)
if nav_menu:
    print("Found #header nav #menu rule:")
    print(nav_menu.group(1))
else:
    print("No #header nav #menu rule found")
