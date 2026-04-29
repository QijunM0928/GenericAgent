import xml.etree.ElementTree as ET
import re

def get_clickable_regions(xml_content):
    root = ET.fromstring(xml_content)
    clickable_nodes = []
    for node in root.iter():
        if node.get('clickable') == 'true':
            bounds_str = node.get('bounds')
            matches = re.findall(r'(\d+)', bounds_str)
            if len(matches) == 4:
                coords = [int(x) for x in matches]
                clickable_nodes.append({
                    'class': node.get('class'),
                    'text': node.get('text'),
                    'bounds': coords
                })
    return clickable_nodes
