import google.generativeai as genai
from PIL import Image
import os
import json

def predict_affordances(image_path):
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
    json_text = response.text.strip().replace('```json', '').replace('```', '')
    return json.loads(json_text)
