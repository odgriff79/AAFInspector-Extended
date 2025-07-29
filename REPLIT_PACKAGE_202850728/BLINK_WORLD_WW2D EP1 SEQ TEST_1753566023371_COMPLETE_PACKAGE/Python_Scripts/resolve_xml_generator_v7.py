#!/usr/bin/env python3
"""
DaVinci Resolve FCPXML Generator V7 - CORRECTED
Generates production-ready FCPXML from AAF JSON data with complete field mapping
"""

import json
import xml.etree.ElementTree as ET
from xml.dom import minidom
from fractions import Fraction
import os
from typing import Dict, List, Any

class ResolveXMLGeneratorV7:
    """V7 XML Generator with corrected field mapping"""
    
    def __init__(self):
        self.asset_counter = 1
        self.asset_id_map = {}
        self.added_assets = set()
    
    def parse_fraction_string(self, frac_str):
        """Parse fraction strings like '333/16' into float values"""
        if isinstance(frac_str, (int, float)):
            return float(frac_str)
        if isinstance(frac_str, str) and '/' in frac_str:
            try:
                return float(Fraction(frac_str))
            except:
                return 0.0
        try:
            return float(frac_str)
        except:
            return 0.0
    
    def generate_xml(self, aaf_data: Dict[str, Any]) -> str:
        """Generate FCPXML from AAF JSON data with complete field mapping"""
        
        # Extract data
        clips = aaf_data.get('clips', [])
        filler_effects = aaf_data.get('filler_effects', [])
        composition_info = aaf_data.get('composition_info', {})
        
        print(f"V7 Generator: Processing {len(clips)} clips and {len(filler_effects)} gaps...")
        
        # Create root FCPXML element
        root = ET.Element('fcpxml', version="1.13")
        
        # Add resources section
        resources = ET.SubElement(root, 'resources')
        
        # Create format definitions for different media types
        format_r3d = ET.SubElement(resources, 'format', 
                                  id="r1", name="FFVideoFormat1080p25", 
                                  frameDuration="1/25s", width="1920", height="1080")
        format_mov = ET.SubElement(resources, 'format', 
                                  id="r2", name="FFVideoFormat1080p25", 
                                  frameDuration="1/25s", width="1920", height="1080")
        format_jpg = ET.SubElement(resources, 'format', 
                                  id="r3", name="FFVideoFormatStill", 
                                  frameDuration="1/25s", width="1920", height="1080")
        
        # Add assets for each unique source file
        self._create_assets(resources, clips)
        
        # Add placeholder assets for filler effects
        self._create_placeholder_assets(resources, filler_effects)
        
        print(f"V7 Generator: Created {len(self.added_assets)} unique assets")
        
        # Create library and event structure
        library = ET.SubElement(root, 'library')
        event = ET.SubElement(library, 'event', name="AAF Import V7")
        
        # Create project and sequence
        project_name = composition_info.get('name', 'Imported Sequence V7')
        project = ET.SubElement(event, 'project', name=project_name)
        
        # Use actual sequence duration from JSON
        seq_duration = composition_info.get('duration', 1000)
        sequence = ET.SubElement(project, 'sequence', 
                               format="r2",
                               duration=f"{seq_duration}/25s")
        
        spine = ET.SubElement(sequence, 'spine')
        
        # Process timeline items
        stats = self._process_timeline_items(spine, clips, filler_effects)
        
        # Generate formatted XML
        xml_content = self._format_xml(root)
        
        print(f"V7 Generator: Generated {len(xml_content):,} character FCPXML")
        print(f"V7 Generator: {stats['total_keyframes']} keyframes from {stats['animated_clips']} clips")
        
        return xml_content
    
    def _create_assets(self, resources, clips):
        """Create asset elements for unique source files"""
        for clip in clips:
            source_file = clip.get('source_file', '')
            if source_file and source_file not in self.added_assets:
                asset_id = f"r{self.asset_counter + 3}"  # Start after format IDs
                self.asset_id_map[source_file] = asset_id
                
                # Determine format based on file extension
                if source_file.lower().endswith('.r3d'):
                    format_ref = "r1"
                elif source_file.lower().endswith(('.mov', '.mp4')):
                    format_ref = "r2"
                else:  # Images
                    format_ref = "r3"
                
                asset = ET.SubElement(resources, 'asset', 
                                    id=asset_id, 
                                    name=os.path.basename(source_file),
                                    format=format_ref)
                
                # Add media-rep with file:// URL
                media_rep = ET.SubElement(asset, 'media-rep', 
                                        kind="original-media",
                                        src=f"file://{source_file}")
                
                self.added_assets.add(source_file)
                self.asset_counter += 1
    
    def _create_placeholder_assets(self, resources, filler_effects):
        """Create placeholder PNG assets for filler effects"""
        for filler in filler_effects:
            effect_type = self._infer_effect_type(filler)
            if effect_type and effect_type != 'None':
                # Create placeholder filename
                placeholder_name = f"{effect_type.lower()}_placeholder.png"
                placeholder_path = f"placeholders/{placeholder_name}"
                
                if placeholder_path not in self.added_assets:
                    asset_id = f"r{self.asset_counter + 3}"
                    self.asset_id_map[placeholder_path] = asset_id
                    
                    asset = ET.SubElement(resources, 'asset', 
                                        id=asset_id, 
                                        name=placeholder_name,
                                        format="r3")  # Still image format
                    
                    # Add media-rep with file:// URL
                    media_rep = ET.SubElement(asset, 'media-rep', 
                                            kind="original-media",
                                            src=f"file://{placeholder_path}")
                    
                    self.added_assets.add(placeholder_path)
                    self.asset_counter += 1
    
    def _infer_effect_type(self, filler):
        """Infer effect type from filler data"""
        # Check explicit effect_type first
        explicit_type = filler.get('effect_type')
        if explicit_type and explicit_type != 'None':
            return explicit_type
        
        # Infer from keyframe parameters
        keyframe_data = filler.get('keyframe_data', {})
        if keyframe_data:
            param_names = list(keyframe_data.keys())
            
            # Common effect type patterns
            if any('POS' in param.upper() or 'SCALE' in param.upper() for param in param_names):
                return 'Transform'
            elif any('CORNER' in param.upper() for param in param_names):
                return '3DWarp'  
            elif any('KEY' in param.upper() or 'MATTE' in param.upper() for param in param_names):
                return 'MatteKey'
            elif any('PAN' in param.upper() or 'ZOOM' in param.upper() for param in param_names):
                return 'PanZoom'
            else:
                return 'Effect'  # Generic effect
        
        return None
    
    def _process_timeline_items(self, spine, clips, filler_effects):
        """Process clips and gaps in timeline order"""
        all_timeline_items = []
        
        # Add clips with their actual timeline positions
        for clip in clips:
            timeline_start = clip.get('timeline_start', 0)
            all_timeline_items.append({
                'type': 'clip',
                'start': timeline_start,
                'data': clip
            })
        
        # Add gaps from filler effects
        for gap in filler_effects:
            timeline_start = gap.get('timeline_start', 0)
            all_timeline_items.append({
                'type': 'gap',
                'start': timeline_start,
                'data': gap
            })
        
        # Sort by timeline position
        all_timeline_items.sort(key=lambda x: x['start'])
        
        print(f"V7 Generator: Processing {len(all_timeline_items)} timeline items...")
        
        # Track keyframe statistics
        total_keyframes_processed = 0
        clips_with_keyframes = 0
        
        # Generate timeline elements
        for item in all_timeline_items:
            if item['type'] == 'clip':
                kf_stats = self._process_clip(spine, item['data'])
                total_keyframes_processed += kf_stats['keyframes']
                if kf_stats['keyframes'] > 0:
                    clips_with_keyframes += 1
            elif item['type'] == 'gap':
                self._process_gap_or_filler(spine, item['data'])
        
        return {
            'total_keyframes': total_keyframes_processed,
            'animated_clips': clips_with_keyframes
        }
    
    def _process_clip(self, spine, clip_data):
        """Process individual clip with keyframes"""
        source_file = clip_data.get('source_file', '')
        asset_id = self.asset_id_map.get(source_file, 'r4')
        
        # Get actual values from JSON
        clip_name = clip_data.get('source_name', os.path.basename(source_file))
        duration = clip_data.get('duration', 25)
        source_in = clip_data.get('source_in', 0)
        
        # Determine if this is a still image
        is_still = source_file.lower().endswith(('.jpg', '.jpeg', '.png', '.tiff', '.tif'))
        
        if is_still:
            # Create video element for still images
            clip_elem = ET.SubElement(spine, 'video', 
                                    ref=asset_id,
                                    name=clip_name,
                                    duration=f"{duration}/25s",
                                    start=f"{source_in}/25s")
        else:
            # Create asset-clip element for video
            clip_elem = ET.SubElement(spine, 'asset-clip', 
                                    ref=asset_id,
                                    name=clip_name,
                                    duration=f"{duration}/25s",
                                    start=f"{source_in}/25s")
        
        # Process keyframes if present (using keyframe_data field)
        keyframe_data = clip_data.get('keyframe_data', {})
        keyframes_processed = 0
        
        if keyframe_data:
            print(f"V7 Generator: Processing keyframes for {clip_name}")
            
            # Create adjust-conform for keyframe animations
            adjust_conform = ET.SubElement(clip_elem, 'adjust-conform')
            adjust_transform = ET.SubElement(adjust_conform, 'adjust-transform')
            
            # Process each parameter type
            for param_name, keyframes in keyframe_data.items():
                if isinstance(keyframes, list) and keyframes:
                    # Map parameter names to FCPXML equivalents
                    if 'POS_X' in param_name.upper() or 'POS_Y' in param_name.upper():
                        fcpxml_param_name = "Position"
                    elif 'SCALE' in param_name.upper():
                        fcpxml_param_name = "Scale"
                    else:
                        fcpxml_param_name = "Transform"
                    
                    # Find or create parameter element
                    param_elem = None
                    for existing_param in adjust_transform.findall('param'):
                        if existing_param.get('name') == fcpxml_param_name:
                            param_elem = existing_param
                            break
                    
                    if param_elem is None:
                        param_elem = ET.SubElement(adjust_transform, 'param', name=fcpxml_param_name)
                    
                    # Add keyframes
                    for kf in keyframes:
                        time_val = self.parse_fraction_string(kf.get('time', 0))
                        value_val = self.parse_fraction_string(kf.get('value', 0))
                        
                        keyframe_elem = ET.SubElement(param_elem, 'keyframe', 
                                                    time=f"{int(time_val * 5)}/5s",  # Use /5s for keyframes
                                                    value=str(value_val),
                                                    curve="linear")
                        keyframes_processed += 1
        
        return {'keyframes': keyframes_processed}
    
    def _process_gap_or_filler(self, spine, gap_data):
        """Process gap/filler element with placeholder support"""
        effect_type = self._infer_effect_type(gap_data)
        gap_duration = gap_data.get('duration', 25)
        
        if effect_type and effect_type != 'None':
            # Create placeholder clip for filler with effects
            placeholder_name = f"{effect_type.lower()}_placeholder.png"
            placeholder_path = f"placeholders/{placeholder_name}"
            asset_id = self.asset_id_map.get(placeholder_path, 'r3')
            
            # Create video element for placeholder
            placeholder_elem = ET.SubElement(spine, 'video', 
                                           ref=asset_id,
                                           name=f"{effect_type} Placeholder",
                                           duration=f"{gap_duration}/25s")
            
            # Add keyframes if available
            keyframe_data = gap_data.get('keyframe_data', {})
            if keyframe_data:
                self._add_keyframes_to_element(placeholder_elem, keyframe_data)
        else:
            # Regular gap
            gap_elem = ET.SubElement(spine, 'gap', 
                                   name="Gap",
                                   duration=f"{gap_duration}/25s")
    
    def _add_keyframes_to_element(self, element, keyframe_data):
        """Add keyframe animation to an element"""
        if keyframe_data:
            adjust_conform = ET.SubElement(element, 'adjust-conform')
            adjust_transform = ET.SubElement(adjust_conform, 'adjust-transform')
            
            # Process each parameter type
            for param_name, keyframes in keyframe_data.items():
                if isinstance(keyframes, list) and keyframes:
                    # Map parameter names to FCPXML equivalents
                    if 'POS_X' in param_name.upper() or 'POS_Y' in param_name.upper():
                        fcpxml_param_name = "Position"
                    elif 'SCALE' in param_name.upper():
                        fcpxml_param_name = "Scale"
                    else:
                        fcpxml_param_name = "Transform"
                    
                    # Find or create parameter element
                    param_elem = None
                    for existing_param in adjust_transform.findall('param'):
                        if existing_param.get('name') == fcpxml_param_name:
                            param_elem = existing_param
                            break
                    
                    if param_elem is None:
                        param_elem = ET.SubElement(adjust_transform, 'param', name=fcpxml_param_name)
                    
                    # Add keyframes
                    for kf in keyframes:
                        time_val = self.parse_fraction_string(kf.get('time', 0))
                        value_val = self.parse_fraction_string(kf.get('value', 0))
                        
                        keyframe_elem = ET.SubElement(param_elem, 'keyframe', 
                                                    time=f"{int(time_val * 5)}/5s",  # Use /5s for keyframes
                                                    value=str(value_val),
                                                    curve="linear")
    
    def _format_xml(self, root):
        """Format XML with proper structure and clean invalid characters"""
        import re
        
        rough_string = ET.tostring(root, 'unicode')
        
        # Clean invalid XML characters - remove problematic Unicode characters like 䐀䕎
        cleaned_string = re.sub(r'[^\x09\x0A\x0D\x20-\uD7FF\uE000-\uFFFD\U00010000-\U0010FFFF]', '', rough_string)
        
        try:
            reparsed = minidom.parseString(cleaned_string)
            pretty_xml = reparsed.toprettyxml(indent="  ")
            
            # Clean up extra whitespace
            lines = [line for line in pretty_xml.split('\n') if line.strip()]
            final_xml = '\n'.join(lines)
            
            # Add proper DOCTYPE
            final_xml = final_xml.replace('<?xml version="1.0" ?>', 
                                         '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>')
            
            return final_xml
        except Exception as e:
            print(f"XML formatting error: {e}")
            # Return basic cleaned XML without pretty formatting
            return cleaned_string

def create_fcpxml_from_json(json_file_path, output_path):
    """Standalone function for direct JSON file processing"""
    
    # Load JSON data
    with open(json_file_path, 'r') as f:
        data = json.load(f)
    
    # Use V7 generator
    generator = ResolveXMLGeneratorV7()
    xml_content = generator.generate_xml(data)
    
    # Write to file
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(xml_content)
    
    # Generate statistics
    clips = data.get('clips', [])
    filler_effects = data.get('filler_effects', [])
    
    stats = {
        'total_clips': len(clips),
        'total_gaps': len(filler_effects),
        'xml_size': len(xml_content),
        'assets_created': len(generator.added_assets)
    }
    
    print(f"Generated FCPXML: {output_path} ({len(xml_content):,} characters)")
    
    return stats

def main():
    """Main execution function for testing"""
    json_file = "aaf_data.json"
    output_file = "resolve_import_v7.xml"
    
    if not os.path.exists(json_file):
        print(f"Error: {json_file} not found")
        return
    
    try:
        stats = create_fcpxml_from_json(json_file, output_file)
        print(f"✅ FCPXML V7 generated successfully: {output_file}")
        print(f"📊 Statistics:")
        print(f"   • {stats['total_clips']} clips processed")
        print(f"   • {stats['total_gaps']} gaps/filler effects")
        print(f"   • {stats['assets_created']} unique assets")
        print(f"   • {stats['xml_size']:,} character XML file")
        
    except Exception as e:
        print(f"❌ Error generating FCPXML: {str(e)}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()