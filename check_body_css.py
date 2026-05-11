import re

with open('assets/css/main.css', 'r') as f:
    content = f.read()

# Look for is-preload class rules
print("=== SEARCHING FOR .is-preload CSS ===\n")

preload_matches = list(re.finditer(r'\.is-preload[^{]*\{([^}]+)\}', content, re.DOTALL))
if preload_matches:
    for i, match in enumerate(preload_matches):
        print(f"Rule {i+1}:")
        print(match.group(0)[:300])
        print('...\n' if len(match.group(0)) > 300 else '\n')
else:
    print("No .is-preload CSS rules found\n")

# Also check body.is-preload
body_preload = re.search(r'body\.is-preload[^{]*\{([^}]+)\}', content, re.DOTALL)
if body_preload:
    print("=== body.is-preload ===")
    print(body_preload.group(1)[:500])
else:
    print("No body.is-preload rule found")

# Check for body rules with header modifications
print("\n=== SEARCHING FOR body CSS ===\n")
body_rules = list(re.finditer(r'body\s*\{([^}]+)\}', content, re.DOTALL))
if body_rules:
    print("First body rule:")
    print(body_rules[0].group(1)[:300])
    if len(body_rules) > 1:
        print(f"\n... plus {len(body_rules)-1} more body rules")
