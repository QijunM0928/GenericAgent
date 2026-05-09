# Mobile Intent-Shadow: UI Affordance Discovery Engine (Spec)

## Concept
A mobile agent capability that discovers "hidden" or "non-semantic" interactive elements on a mobile UI that are not exposed via standard Accessibility (A11y) trees. It uses vision-language models (VLM) and visual heuristics to predict interactability (affordance) even for blank spaces, custom drawn widgets, or game-like interfaces.

## Why it's novel (Token Plan Value)
Traditional mobile automation relies on A11y trees (XML/JSON dumps of the UI). If a developer uses a custom Canvas, a game engine, or simply a poorly tagged `div/View` with an `onClick` handler, the A11y tree sees it as "unclickable" or just a blank rect.
This engine bypasses the A11y tree completely. It looks at the screenshot like a human does, infers context, and generates a "Probability Heatmap of Interactability". This is a core missing piece for True Autonomous Mobile Agents.

## Architecture
1. **Input Layer**: Takes a raw mobile screenshot (and optionally the raw A11y tree for comparison/diffing).
2. **Vision Analysis Layer**:
    *   **VLM Prompting**: Asks a model like Gemini 1.5 Pro to identify all interactive regions, specifically looking for subtle cues (shadows, layout spacing, implied buttons in games).
    *   **Heuristic Fallback**: Basic edge-detection/contour-finding (OpenCV) to find isolated shapes that look like buttons but lack text.
3. **Diffing Engine**: Compares the VLM's predicted clickable bounding boxes against the A11y tree's clickable nodes.
4. **Output Layer (The "Shadow")**: Generates a new metadata layer (an overlay image and a JSON list of bounding boxes) representing the "Hidden Affordances" — things the VLM thinks are clickable but the OS doesn't know about.

## Success Criteria for MVP
1. Can process a screenshot of a mobile app.
2. Can output a visual overlay highlighting predicted clickable areas.
3. Can successfully identify at least one "hidden" interactive element (e.g., a custom icon without an A11y label) that a standard XML parser would miss.
