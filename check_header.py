import requests

# Get the page
r = requests.get('https://www.jackcapstaff.com/rehearsal-schedule/my', timeout=20)

# Look for nav element
nav_start = r.text.find('<nav id="nav">')
if nav_start > -1:
    nav_end = r.text.find('</nav>', nav_start) + 6
    nav_section = r.text[nav_start:nav_end]
    print('NAV SECTION FOUND - First 600 chars:')
    print(nav_section[:600])
    print('\nNav element: YES, in HTML')
else:
    print('Nav element: NO, NOT in HTML')

# Check if header exists
header_start = r.text.find('<header id="header"')
if header_start > -1:
    print('Header element: YES, in HTML')
else:
    print('Header element: NO, NOT in HTML')

# Check if div#menu exists
menu_start = r.text.find('id="menu"')
if menu_start > -1:
    print('Menu div: YES, in HTML')
    # Get context around it
    start = max(0, menu_start - 100)
    end = min(len(r.text), menu_start + 200)
    print('Context:', r.text[start:end])
else:
    print('Menu div: NO, NOT in HTML')
