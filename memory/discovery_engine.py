# discovery_engine.py - Diffing engine to find "hidden" affordances
# Compares VLM predictions against Accessibility (A11y) tree data.

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
    """
    Calculate the Intersection over Union (IoU) of two bounding boxes.
    Boxes are expected to be in [x1, y1, x2, y2] format.
    """
    xA = max(boxA[0], boxB[0])
    yA = max(boxA[1], boxB[1])
    xB = min(boxA[2], boxB[2])
    yB = min(boxA[3], boxB[3])
    
    # Compute the area of intersection
    interWidth = max(0, xB - xA + 1)
    interHeight = max(0, yB - yA + 1)
    interArea = interWidth * interHeight
    
    # Compute the area of both bounding boxes
    boxAArea = (boxA[2] - boxA[0] + 1) * (boxA[3] - boxA[1] + 1)
    boxBArea = (boxB[2] - boxB[0] + 1) * (boxB[3] - boxB[1] + 1)
    
    # Compute the IoU
    unionArea = float(boxAArea + boxBArea - interArea)
    if unionArea == 0:
        return 0
        
    return interArea / unionArea
