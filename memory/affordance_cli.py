import cv2
import numpy as np
import argparse
import os
import json
from memory.utils.a11y_parser import get_clickable_regions
from memory.affordance_vlm import predict_affordances
from memory.discovery_engine import find_hidden_affordances

def draw_affordances(image_path, hidden_regions, output_path):
    """
    Draws red rectangles around hidden regions and saves the image.
    """
    img = cv2.imread(image_path)
    if img is None:
        print(f"Error: Could not read image at {image_path}")
        return
        
    for reg in hidden_regions:
        b = reg['bounds']
        # OpenCV uses (x1, y1), (x2, y2)
        cv2.rectangle(img, (b[0], b[1]), (b[2], b[3]), (0, 0, 255), 3) # Red for hidden
        cv2.putText(img, "HIDDEN", (b[0], b[1]-10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 2)
        
    cv2.imwrite(output_path, img)
    print(f"Saved visualization to {output_path}")

def main():
    parser = argparse.ArgumentParser(description="Mobile Intent-Shadow: Highlight hidden affordances on a screenshot.")
    parser.add_argument("--image", required=True, help="Path to the screenshot image (PNG/JPG).")
    parser.add_argument("--xml", required=True, help="Path to the Android A11y XML file.")
    parser.add_argument("--output", default="hidden_affordances.png", help="Path to save the highlighted image.")
    parser.add_argument("--threshold", type=float, default=0.3, help="IoU threshold for matching regions.")
    
    args = parser.parse_args()
    
    if not os.path.exists(args.image):
        print(f"Error: Image file not found: {args.image}")
        return
    if not os.path.exists(args.xml):
        print(f"Error: XML file not found: {args.xml}")
        return

    print(f"--- Starting Affordance Discovery ---")
    print(f"Image: {args.image}")
    print(f"XML:   {args.xml}")
    
    # 1. Parse A11y regions (Explicit affordances)
    try:
        with open(args.xml, 'r', encoding='utf-8') as f:
            xml_content = f.read()
        a11y_regions = get_clickable_regions(xml_content)
        print(f"[1/4] A11y Parser: Found {len(a11y_regions)} explicit clickable regions.")
    except Exception as e:
        print(f"Error parsing XML: {e}")
        return
    
    # 2. Predict VLM regions (Perceived affordances)
    print("[2/4] VLM Predictor: Analyzing visual cues (calling Gemini)...")
    try:
        vlm_regions = predict_affordances(args.image)
        print(f"      Found {len(vlm_regions)} perceived interactive regions.")
    except Exception as e:
        print(f"Error calling VLM: {e}")
        return
    
    # 3. Find hidden affordances (The Gap)
    print(f"[3/4] Discovery Engine: Diffing regions (IoU threshold={args.threshold})...")
    hidden_regions = find_hidden_affordances(a11y_regions, vlm_regions, iou_threshold=args.threshold)
    print(f"      Identified {len(hidden_regions)} hidden affordances (regions missing from A11y tree).")
    
    # 4. Draw and save
    if hidden_regions:
        print(f"[4/4] Visualizer: Drawing {len(hidden_regions)} regions on output image...")
        draw_affordances(args.image, hidden_regions, args.output)
        
        # Also print the findings
        for i, reg in enumerate(hidden_regions):
            print(f"      - Hidden #{i+1}: {reg.get('label', 'Unknown')} at {reg['bounds']}")
    else:
        print("[4/4] Visualizer: No hidden affordances found to draw.")

if __name__ == "__main__":
    main()
