# Mobile Intent-Shadow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a tool that identifies "hidden" interactive elements on a mobile screen by comparing VLM (Vision-Language Model) predictions against standard Accessibility (A11y) tree data.

**Architecture:** The system captures a screenshot and its A11y XML. It parses the XML to find "explicit" clickable regions. Simultaneously, it sends the screenshot to a VLM (Gemini 1.5 Pro) to identify "perceived" interactive regions based on visual cues. A diffing engine then highlights the gap (hidden affordances).

**Tech Stack:** Python, OpenCV, PIL (Pillow), Gemini API.

---

### Task 1: Basic XML Parser for Clickable Regions

**Files:**
- Create: `memory/utils/a11y_parser.py`
- Test: `tests/test_a11y_parser.py`

- [ ] **Step 1: Create the A11y XML parser utility**

```python
import xml.etree.ElementTree as ET
import re

def get_clickable_regions(xml_content):
    """
    Parses Android A11y XML and returns a list of clickable bounding boxes.
    Format: [{'text': '...', 'bounds': [x1, y1, x2, y2]}]
    """
    root = ET.fromstring(xml_content)
    clickable_nodes = []
    
    for node in root.iter():
        if node.get('clickable') == 'true':
            bounds_str = node.get('bounds')
            # Extract [x1,y1][x2,y2]
            matches = re.findall(r'(\d+)', bounds_str)
            if len(matches) == 4:
                coords = [int(x) for x in matches]
                clickable_nodes.append({
                    'class': node.get('class'),
                    'text': node.get('text'),
                    'bounds': coords
                })
    return clickable_nodes
```

- [ ] **Step 2: Write tests with mock XML**

```python
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
```

- [ ] **Step 3: Run tests to verify**

Run: `pytest tests/test_a11y_parser.py`

- [ ] **Step 4: Commit**

```bash
git add memory/utils/a11y_parser.py tests/test_a11y_parser.py
git commit -m "feat: add A11y XML parser for clickable regions"
```

---

### Task 2: VLM-based Affordance Predictor

**Files:**
- Create: `memory/affordance_vlm.py`

- [ ] **Step 1: Implement VLM calling logic**

```python
import google.generativeai as genai
from PIL import Image
import os
import json

def predict_affordances(image_path):
    """
    Sends screenshot to VLM and asks for predicted interactive regions.
    Returns: List of [x1, y1, x2, y2, label] in pixel coordinates.
    """
    model = genai.GenerativeModel('gemini-1.5-pro')
    img = Image.open(image_path)
    width, height = img.size
    
    prompt = f"""
    You are an expert mobile UI analyzer. Look at this screenshot (size {width}x{height}).
    Identify all interactive regions (buttons, inputs, clickable cards, custom icons).
    Pay special attention to areas that look clickable but might not be standard buttons (e.g. blank areas in a list, custom drawn widgets).
    Return the result as a JSON list of objects: {{"label": "...", "bounds": [x1, y1, x2, y2]}}.
    Coordinates must be in actual pixels based on {width}x{height}.
    ONLY return the JSON block.
    """
    
    response = model.generate_content([prompt, img])
    # Clean up response text to find JSON
    json_text = response.text.strip().replace('```json', '').replace('```', '')
    return json.loads(json_text)
```

- [ ] **Step 2: Commit**

```bash
git add memory/affordance_vlm.py
git commit -m "feat: add VLM-based affordance predictor"
```

---

### Task 3: The Diffing and Discovery Engine

**Files:**
- Create: `memory/discovery_engine.py`

- [ ] **Step 1: Implement the Diff logic**

```python
def find_hidden_affordances(a11y_regions, vlm_regions, iou_threshold=0.3):
    """
    Compares VLM predictions against A11y regions.
    Returns regions predicted by VLM that are NOT in A11y tree.
    """
    hidden = []
    for v_reg in vlm_regions:
        is_known = False
        v_bounds = v_reg['bounds']
        
        for a_reg in a11y_regions:
            a_bounds = a_reg['bounds']
            # Calculate IoU
            if calculate_iou(v_bounds, a_bounds) > iou_threshold:
                is_known = True
                break
        
        if not is_known:
            hidden.append(v_reg)
    return hidden

def calculate_iou(boxA, boxB):
    xA = max(boxA[0], boxB[0])
    yA = max(boxA[1], boxB[1])
    xB = min(boxA[2], boxB[2])
    yB = min(boxA[3], boxB[3])
    interArea = max(0, xB - xA + 1) * max(0, yB - yA + 1)
    boxAArea = (boxA[2] - boxA[0] + 1) * (boxA[3] - boxA[1] + 1)
    boxBArea = (boxB[2] - boxB[0] + 1) * (boxB[3] - boxB[1] + 1)
    return interArea / float(boxAArea + boxBArea - interArea)
```

- [ ] **Step 2: Commit**

```bash
git add memory/discovery_engine.py
git commit -m "feat: add hidden affordance diffing logic"
```

---

### Task 4: Visualization and CLI Entrypoint

**Files:**
- Create: `memory/affordance_cli.py`

- [ ] **Step 1: Implement visual overlay drawing**

```python
import cv2
import numpy as np

def draw_affordances(image_path, hidden_regions, output_path):
    img = cv2.imread(image_path)
    for reg in hidden_regions:
        b = reg['bounds']
        cv2.rectangle(img, (b[0], b[1]), (b[2], b[3]), (0, 0, 255), 3) # Red for hidden
        cv2.putText(img, "HIDDEN", (b[0], b[1]-10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 2)
    cv2.imwrite(output_path, img)
```

- [ ] **Step 2: Commit**

```bash
git add memory/affordance_cli.py
git commit -m "feat: add visualization and CLI for affordance discovery"
```
