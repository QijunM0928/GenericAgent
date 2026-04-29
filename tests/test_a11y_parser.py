import pytest
from memory.utils.a11y_parser import get_clickable_regions

def test_parse_clickable_bounds():
    mock_xml = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
    <hierarchy rotation="0">
        <node index="0" text="Button 1" class="android.widget.Button" bounds="[0,0][100,100]" clickable="true" />
        <node index="1" text="" class="android.widget.ImageView" bounds="[200,200][300,300]" clickable="false" />
    </hierarchy>"""
    regions = get_clickable_regions(mock_xml)
    assert len(regions) == 1
    assert regions[0]['bounds'] == [0, 0, 100, 100]
