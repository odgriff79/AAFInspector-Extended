import json
import sys

def get_child_property(node, child_name, property_name='value'):
    """Helper function to find a direct child's value by its name."""
    if not isinstance(node, dict) or 'children' not in node:
        return None
    for child in node['children']:
        if isinstance(child, dict) and child.get('name') == child_name:
            return child.get(property_name)
    return None

def find_node_by_path(node, path):
    """Finds a nested node by following a list of names."""
    current = node
    for key in path:
        if isinstance(current, dict) and 'children' in current:
            found_child = None
            for child in current['children']:
                if isinstance(child, dict) and child.get('name') == key:
                    found_child = child
                    break
            current = found_child
            if current is None: return None
        else:
            return None
    return current

def parse_keyframes(varying_value_node):
    """Parses a VaryingValue node to extract keyframes."""
    keyframes = []
    points_to_check = varying_value_node.get('children', [])
    
    cp_wrapper = get_child_property(varying_value_node, 'ControlPoints', property_name=None)
    if cp_wrapper:
        points_to_check = cp_wrapper.get('children', [])

    for node in points_to_check:
        if isinstance(node, dict) and node.get('class') == 'ControlPoint':
            time = get_child_property(node, 'Time')
            value = get_child_property(node, 'Value')
            if time is not None and value is not None:
                keyframes.append({'time_offset': time, 'value': value})
    return keyframes

def parse_effect(effect_node):
    """Parses an OperationGroup node to extract its name and animated parameters."""
    effect_info = {"name": effect_node.get('name', "Unknown Effect"), "animated_params": {}}
    params_list_node = get_child_property(effect_node, 'Parameters', property_name=None)

    if not params_list_node or 'children' not in params_list_node:
        return effect_info

    for param_node in params_list_node['children']:
        if isinstance(param_node, dict) and param_node.get('class') == 'VaryingValue':
            param_name = param_node.get('name', 'Unknown Parameter')
            keyframes = parse_keyframes(param_node)
            if keyframes:
                effect_info['animated_params'][param_name] = keyframes
    return effect_info

def parse_components(components_node):
    """Parses a 'Components' node to build a timeline of events."""
    timeline = []
    current_time_frames = 0
    if not components_node or 'children' not in components_node:
        return timeline

    for component in components_node['children']:
        if not isinstance(component, dict): continue

        if component.get('class') == 'SourceClip':
            duration = int(get_child_property(component, 'Length', 'value') or 0)
            source_in_tc = get_child_property(component, 'StartTime', 'value')

            clip_name = 'Unknown Clip'
            source_mob_ref_node = get_child_property(component, 'Source Mob Ref', property_name=None)
            if source_mob_ref_node and 'children' in source_mob_ref_node and source_mob_ref_node['children']:
                actual_mob_node = source_mob_ref_node['children'][0]
                clip_name = actual_mob_node.get('name', clip_name)

            clip_event = {
                "event_number": len(timeline) + 1, "type": "Clip", "name": clip_name,
                "start_time_frames": current_time_frames, "duration_frames": duration,
                "source_in_tc": source_in_tc, "effects": []
            }
            timeline.append(clip_event)
            current_time_frames += duration

        elif component.get('class') == 'OperationGroup':
            if not timeline: continue
            effect = parse_effect(component)
            if 'animated_params' in effect and effect['animated_params']:
                timeline[-1]['effects'].append(effect)
    return timeline

def find_components_recursively(node):
    """Recursive helper to find the 'Components' node within any structure."""
    if isinstance(node, dict) and node.get('name') == 'Components' and 'children' in node:
        return node
    
    if isinstance(node, dict) and 'children' in node:
        for child in node['children']:
            result = find_components_recursively(child)
            if result:
                return result
    return None

def parse_composition_mob(comp_mob_node):
    """Parses a single CompositionMob by iterating through ALL its slots."""
    slots_node = get_child_property(comp_mob_node, 'Slots', property_name=None)
    if not slots_node or 'children' not in slots_node:
        return None

    for slot in slots_node['children']:
        components_node = find_components_recursively(slot)
        if components_node:
            timeline = parse_components(components_node)
            if timeline:
                return timeline
    
    return None

def main(json_path):
    """Main function to load JSON and initiate parsing."""
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"Error: The file '{json_path}' was not found.")
        return
    except json.JSONDecodeError:
        print(f"Error: The file '{json_path}' is not a valid JSON file.")
        return
    
    # Path confirmed by the final diagnostic log
    path_to_mobs = ['Header', 'Header', 'Content', 'ContentStorage', 'Mobs']
    
    mobs_node = find_node_by_path(data, path_to_mobs)
    if not mobs_node or 'children' not in mobs_node:
        print("Error: Could not find the 'Mobs' list at the expected path.")
        return
        
    all_mobs = mobs_node['children']
    all_comp_mobs = [mob for mob in all_mobs if isinstance(mob, dict) and mob.get('class') == 'CompositionMob']

    if not all_comp_mobs:
        print("Error: Could not find any 'CompositionMob' in the Mobs list.")
        return

    best_timeline = None
    for comp_mob in all_comp_mobs:
        parsed_timeline = parse_composition_mob(comp_mob)
        
        if parsed_timeline:
            is_good_parse = len(parsed_timeline) > 1 or \
                           (len(parsed_timeline) == 1 and parsed_timeline[0].get('name') != 'Unknown Clip')
                           
            if is_good_parse:
                if best_timeline is None or len(parsed_timeline) > len(best_timeline):
                    best_timeline = parsed_timeline
                
    if best_timeline:
        print(json.dumps(best_timeline, indent=4))
    else:
        print("Error: Found Composition Mobs, but failed to parse any into a valid timeline.")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python your_script_name.py <path_to_your_exported.json>")
    else:
        main(sys.argv[1])