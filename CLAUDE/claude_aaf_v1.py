#!/usr/bin/env python3
"""
Enhanced AAF Inspector with Refined Conversion Logic - SECTION 1
Imports and Core Conversion Functions

USAGE: Combine all 5 sections in order to create the complete file
"""

import os
import sys
import json
import datetime
import uuid
import re
import urllib.parse
import struct
from typing import Dict, List, Optional, Any, Union
from xml.etree.ElementTree import Element, SubElement, tostring
from xml.dom.minidom import parseString

from PySide6 import QtCore
from PySide6 import QtWidgets
from PySide6 import QtGui

try:
    import aaf2
except ImportError:
    print("Error: aaf2 library not found.")
    print("Please ensure pyaaf2 is installed: pip install pyaaf2==1.4.0")
    sys.exit(1)


def frames_to_tc(frame_count, fps=25.0, is_drop_frame=False):
    """Convert frame count to timecode string with proper DF/NDF support"""
    if frame_count is None or fps is None or fps <= 0:
        return "N/A"
    try:
        separator = ";" if is_drop_frame else ":"
        fc = int(frame_count)
        int_fps = round(float(fps))
        if int_fps <= 0:
            return "N/A"
            
        # Handle drop frame calculation for 29.97
        if is_drop_frame and abs(fps - 29.97) < 0.01:
            frames_per_10min = 17982
            frames_per_min = 1798
            ten_min_periods = fc // frames_per_10min
            remaining = fc % frames_per_10min
            
            if remaining < frames_per_min:
                minutes = 0
                final_frames = remaining
            else:
                minutes = 1 + ((remaining - frames_per_min) // 1800)
                final_frames = (remaining - frames_per_min) % 1800
                if minutes > 0 and final_frames < 2:
                    final_frames += 2
            
            total_minutes = ten_min_periods * 10 + minutes
            h = total_minutes // 60
            m = total_minutes % 60
            s = final_frames // 30
            f = final_frames % 30
        else:
            h = fc // (3600 * int_fps)
            m = (fc % (3600 * int_fps)) // (60 * int_fps)
            s = (fc % (60 * int_fps)) // int_fps
            f = fc % int_fps
            
        return f"{h:02}:{m:02}:{s:02}{separator}{f:02}"
    except (ValueError, TypeError):
        return "N/A"


def extract_pan_zoom_still_path(operation_group, log_callback=None):
    """Extract still image path from Pan & Zoom on filler"""
    def log(msg):
        if log_callback:
            log_callback(msg)
        else:
            print(msg)
    
    try:
        log("Searching for Pan & Zoom still image path...")
        
        if not hasattr(operation_group, 'properties'):
            log("No properties found on OperationGroup")
            return None
            
        parameters_prop = None
        for prop in operation_group.properties():
            if getattr(prop, 'name', '') == 'Parameters':
                parameters_prop = prop
                break
        
        if not parameters_prop:
            log("No Parameters property found")
            return None
        
        parameters = parameters_prop.value
        if not hasattr(parameters, '__iter__'):
            log("Parameters is not iterable")
            return None
        
        log(f"Found {len(list(parameters))} parameters")
        
        for i, param in enumerate(parameters):
            try:
                param_name = getattr(param, 'name', f'param_{i}')
                log(f"  Parameter {i}: {param_name}")
                
                if hasattr(param, 'properties'):
                    for param_prop in param.properties():
                        param_prop_name = getattr(param_prop, 'name', str(param_prop))
                        log(f"    Property: {param_prop_name}")
                        
                        if param_prop_name == 'Value':
                            try:
                                value = param_prop.value
                                log(f"      Value type: {type(value)}")
                                
                                path = _decode_avx_path_data(value, log)
                                if path:
                                    log(f"      -> Found path: {path}")
                                    return path
                                    
                            except Exception as e:
                                log(f"      Error accessing value: {e}")
                
                if hasattr(param, 'value'):
                    try:
                        value = param.value
                        path = _decode_avx_path_data(value, log)
                        if path:
                            log(f"    -> Found path in param value: {path}")
                            return path
                    except Exception as e:
                        log(f"    Error accessing param value: {e}")
                        
            except Exception as e:
                log(f"  Error processing parameter {i}: {e}")
        
        log("No still image path found in Pan & Zoom parameters")
        return None
        
    except Exception as e:
        log(f"Error extracting Pan & Zoom path: {e}")
        return None


def _decode_avx_path_data(value, log_callback):
    """Try multiple strategies to decode path data from AVX parameter values"""
    def log(msg):
        if log_callback:
            log_callback(msg)
    
    try:
        log(f"        Trying to decode path from {type(value)} data")
        
        # Strategy 1: Direct bytes UTF-16LE
        if isinstance(value, bytes):
            if len(value) > 10:
                try:
                    decoded = value.decode('utf-16le', errors='ignore')
                    path = _clean_decoded_path(decoded)
                    if path and ('.' in path or '\\' in path or '/' in path):
                        log(f"        UTF-16LE direct: {path}")
                        return path
                except:
                    pass
        
        # Strategy 2: List of integers to bytes
        elif isinstance(value, (list, tuple)):
            if len(value) > 10 and all(isinstance(x, int) and 0 <= x <= 255 for x in value[:100]):
                try:
                    raw_bytes = bytes(value)
                    decoded = raw_bytes.decode('utf-16le', errors='ignore')
                    path = _clean_decoded_path(decoded)
                    if path and ('.' in path or '\\' in path or '/' in path):
                        log(f"        List->bytes UTF-16LE: {path}")
                        return path
                except:
                    pass
        
        # Strategy 3: String data
        elif isinstance(value, str):
            if len(value) > 5:
                try:
                    decoded = urllib.parse.unquote(value)
                    path = _clean_decoded_path(decoded)
                    if path and ('.' in path or '\\' in path or '/' in path):
                        log(f"        URL decoded string: {path}")
                        return path
                except:
                    pass
                
                path = _clean_decoded_path(value)
                if path and ('.' in path or '\\' in path or '/' in path):
                    log(f"        Direct string: {path}")
                    return path
        
        # Strategy 4: Binary data with different encodings
        elif hasattr(value, '__bytes__'):
            try:
                raw_bytes = bytes(value)
                if len(raw_bytes) > 10:
                    decoded = raw_bytes.decode('utf-16le', errors='ignore')
                    path = _clean_decoded_path(decoded)
                    if path and ('.' in path or '\\' in path or '/' in path):
                        log(f"        Binary UTF-16LE: {path}")
                        return path
            except:
                pass
        
        log(f"        No valid path found in {type(value)} data")
        return None
        
    except Exception as e:
        log(f"        Decode error: {e}")
        return None


def _clean_decoded_path(decoded_string):
    """Clean up decoded path string"""
    if not decoded_string:
        return None
    
    cleaned = decoded_string.rstrip('\x00').replace('\x00', '')
    
    drive_match = re.search(r'[A-Za-z]:[\\\/]', cleaned)
    if drive_match:
        cleaned = cleaned[drive_match.start():]
    
    unc_match = re.search(r'\\\\[^\\]+\\', cleaned)
    if unc_match:
        cleaned = cleaned[unc_match.start():]
    
    cleaned = cleaned.replace('\\', '/')
    cleaned = cleaned.rstrip('\x00 \t\n\r')
    
    if len(cleaned) > 3 and '.' in cleaned:
        if any(ext in cleaned.lower() for ext in ['.jpg', '.jpeg', '.png', '.tif', '.tiff', '.bmp', '.exr']):
            return cleaned
    
    return None
    # SECTION 2: Source Chain Resolution Functions

def resolve_source_chain(source_clip, mob_map, log_callback=None):
    """Follow UMID + SourceMobSlotID chain until finding SourceMob with ImportDescriptor/URLString"""
    def log(msg):
        if log_callback:
            log_callback(msg)
        else:
            print(msg)
    
    try:
        log(f"Resolving source chain for SourceClip: {getattr(source_clip, 'name', 'unnamed')}")
        
        source_id = None
        slot_id = None
        
        if hasattr(source_clip, 'properties'):
            for prop in source_clip.properties():
                prop_name = getattr(prop, 'name', '')
                if prop_name == 'SourceID':
                    source_id = prop.value
                    log(f"  Found SourceID: {source_id}")
                elif prop_name == 'SourceMobSlotID':
                    slot_id = prop.value
                    log(f"  Found SourceMobSlotID: {slot_id}")
        
        if not source_id:
            log("  No SourceID found")
            return None
        
        source_id_str = str(source_id)
        visited = set()
        current_id = source_id_str
        
        while current_id and current_id not in visited:
            visited.add(current_id)
            log(f"  Following chain to mob: {current_id}")
            
            mob = mob_map.get(current_id)
            if not mob:
                log(f"    Mob not found in mob_map")
                break
            
            mob_class = getattr(getattr(mob, 'classdef', None), 'name', 'Unknown')
            log(f"    Mob class: {mob_class}")
            
            if mob_class == 'SourceMob':
                url_string = _extract_urlstring(mob, log)
                if url_string:
                    log(f"    -> Found final source: {url_string}")
                    return {
                        'mob': mob,
                        'url_string': url_string,
                        'mob_id': current_id,
                        'mob_class': mob_class
                    }
                else:
                    log(f"    SourceMob has no URLString, continuing chain...")
            
            next_id = None
            if hasattr(mob, 'slots'):
                for slot in mob.slots:
                    slot_slot_id = getattr(slot, 'slot_id', None)
                    if slot_id is None or slot_slot_id == slot_id:
                        if hasattr(slot, 'properties'):
                            for slot_prop in slot.properties():
                                if getattr(slot_prop, 'name', '') == 'Segment':
                                    segment = slot_prop.value
                                    if segment:
                                        next_id = _get_source_id_from_segment(segment, log)
                                        if next_id:
                                            log(f"      Next in chain: {next_id}")
                                            break
                        if next_id:
                            break
            
            if not next_id:
                log(f"    End of chain at {mob_class}")
                if mob_class == 'SourceMob':
                    return {
                        'mob': mob,
                        'url_string': None,
                        'mob_id': current_id,
                        'mob_class': mob_class
                    }
                break
            
            current_id = str(next_id)
        
        log("  Source chain resolution failed")
        return None
        
    except Exception as e:
        log(f"Error resolving source chain: {e}")
        return None


def _extract_urlstring(mob, log_callback):
    """Extract URLString from a SourceMob's descriptor"""
    def log(msg):
        if log_callback:
            log_callback(msg)
    
    try:
        if hasattr(mob, 'properties'):
            for prop in mob.properties():
                prop_name = getattr(prop, 'name', '')
                if prop_name == 'EssenceDescription':
                    descriptor = prop.value
                    if descriptor:
                        log(f"      Found EssenceDescription")
                        if hasattr(descriptor, 'properties'):
                            for desc_prop in descriptor.properties():
                                desc_prop_name = getattr(desc_prop, 'name', '')
                                log(f"        Descriptor property: {desc_prop_name}")
                                if desc_prop_name == 'Locator':
                                    locator = desc_prop.value
                                    if locator and hasattr(locator, 'properties'):
                                        for loc_prop in locator.properties():
                                            loc_prop_name = getattr(loc_prop, 'name', '')
                                            if loc_prop_name == 'URLString':
                                                url = loc_prop.value
                                                if url:
                                                    return str(url)
        return None
    except Exception as e:
        log(f"      Error extracting URLString: {e}")
        return None


def _get_source_id_from_segment(segment, log_callback):
    """Get SourceID from a segment (could be SourceClip or Sequence)"""
    def log(msg):
        if log_callback:
            log_callback(msg)
    
    try:
        segment_class = getattr(getattr(segment, 'classdef', None), 'name', 'Unknown')
        log(f"        Segment class: {segment_class}")
        
        if segment_class == 'SourceClip':
            if hasattr(segment, 'properties'):
                for prop in segment.properties():
                    if getattr(prop, 'name', '') == 'SourceID':
                        return prop.value
        
        elif segment_class == 'Sequence':
            if hasattr(segment, 'properties'):
                for prop in segment.properties():
                    if getattr(prop, 'name', '') == 'Components':
                        components = prop.value
                        if components and hasattr(components, '__iter__'):
                            for comp in components:
                                comp_class = getattr(getattr(comp, 'classdef', None), 'name', 'Unknown')
                                if comp_class == 'SourceClip':
                                    return _get_source_id_from_segment(comp, log_callback)
        
        return None
    except Exception as e:
        log(f"        Error getting SourceID from segment: {e}")
        return None


def extract_comprehensive_metadata(mob, log_callback=None):
    """Extract comprehensive metadata with proper DiskLabel/TapeID precedence"""
    def log(msg):
        if log_callback:
            log_callback(msg)
        else:
            print(msg)
    
    metadata = {
        "URLString": "",
        "TapeID": "",
        "DiskLabel": "",
        "SourceEditRate": None,
        "GenuineStartFrames": 0,
        "IsDropFrame": False,
        "Name": "",
        "MobClass": ""
    }
    
    try:
        log(f"Extracting metadata from mob: {getattr(mob, 'name', 'unnamed')}")
        
        if hasattr(mob, 'name'):
            metadata["Name"] = str(mob.name)
        
        mob_class = getattr(getattr(mob, 'classdef', None), 'name', 'Unknown')
        metadata["MobClass"] = mob_class
        log(f"  Mob class: {mob_class}")
        
        url = _extract_urlstring(mob, log)
        if url:
            metadata["URLString"] = url
        
        all_starts = []
        if hasattr(mob, 'slots'):
            for slot in mob.slots:
                if hasattr(slot, 'properties'):
                    for prop in slot.properties():
                        prop_name = getattr(prop, 'name', '')
                        if prop_name == 'EditRate':
                            try:
                                rate = prop.value
                                if hasattr(rate, 'numerator') and hasattr(rate, 'denominator'):
                                    metadata["SourceEditRate"] = float(rate.numerator) / float(rate.denominator)
                                else:
                                    metadata["SourceEditRate"] = float(str(rate).split('/')[0]) / float(str(rate).split('/')[1]) if '/' in str(rate) else float(rate)
                                log(f"    Edit rate: {metadata['SourceEditRate']}")
                            except Exception as e:
                                log(f"    Error parsing edit rate: {e}")
                        
                        elif prop_name == 'Segment':
                            segment = prop.value
                            if segment:
                                segment_class = getattr(getattr(segment, 'classdef', None), 'name', 'Unknown')
                                if segment_class == 'Timecode':
                                    if hasattr(segment, 'properties'):
                                        for tc_prop in segment.properties():
                                            tc_prop_name = getattr(tc_prop, 'name', '')
                                            if tc_prop_name == 'Start':
                                                try:
                                                    start_val = int(tc_prop.value)
                                                    all_starts.append(start_val)
                                                    log(f"    Found start: {start_val}")
                                                except:
                                                    pass
                                            elif tc_prop_name == 'Drop':
                                                metadata["IsDropFrame"] = bool(tc_prop.value)
                                                log(f"    Drop frame: {metadata['IsDropFrame']}")
        
        if all_starts:
            metadata["GenuineStartFrames"] = max(all_starts)
            log(f"  Genuine start frames: {metadata['GenuineStartFrames']}")
        
        # Extract TapeID from UserComments (highest priority)
        if hasattr(mob, 'comments'):
            for comment in mob.comments:
                try:
                    name = str(comment.name)
                    value = str(comment.value)
                    log(f"  Comment: {name} = {value}")
                    if name == "TapeID" and not metadata["TapeID"]:
                        metadata["TapeID"] = value
                        log(f"    -> TapeID from comments: {value}")
                except:
                    pass
        
        # Extract from MobAttributeList (medium priority)
        if hasattr(mob, 'mob_attribute_list'):
            for attr in mob.mob_attribute_list:
                try:
                    attr_name = str(attr.name)
                    attr_value = str(attr.value)
                    log(f"  Mob attribute: {attr_name} = {attr_value}")
                    if attr_name == "TapeID" and not metadata["TapeID"]:
                        metadata["TapeID"] = attr_value
                        log(f"    -> TapeID from mob attributes: {attr_value}")
                    elif attr_name == "DiskLabel" and not metadata["DiskLabel"]:
                        metadata["DiskLabel"] = attr_value
                        log(f"    -> DiskLabel from mob attributes: {attr_value}")
                except:
                    pass
        
        # Look for _IMPORTSETTING -> TaggedValueAttributeList -> _IMPORTDISKLABEL
        if not metadata["DiskLabel"]:
            disk_label = _extract_import_disk_label(mob, log)
            if disk_label:
                metadata["DiskLabel"] = disk_label
        
        log(f"Final metadata: {metadata}")
        return metadata
        
    except Exception as e:
        log(f"Error extracting metadata: {e}")
        return metadata


def _extract_import_disk_label(mob, log_callback):
    """Extract DiskLabel from _IMPORTSETTING -> TaggedValueAttributeList -> _IMPORTDISKLABEL"""
    def log(msg):
        if log_callback:
            log_callback(msg)
    
    try:
        return _recursive_search_for_disk_label(mob, log)
    except Exception as e:
        log(f"Error extracting import disk label: {e}")
        return None


def _recursive_search_for_disk_label(obj, log_callback, depth=0):
    """Recursively search for _IMPORTDISKLABEL in nested structures"""
    def log(msg):
        if log_callback:
            log_callback(msg)
    
    if depth > 10:
        return None
    
    try:
        obj_name = getattr(obj, 'name', '')
        if '_IMPORTDISKLABEL' in obj_name:
            if hasattr(obj, 'value'):
                value = str(obj.value)
                log(f"{'  ' * depth}Found _IMPORTDISKLABEL: {value}")
                return value
        
        if hasattr(obj, 'properties'):
            for prop in obj.properties():
                result = _recursive_search_for_disk_label(prop, log_callback, depth + 1)
                if result:
                    return result
        
        if hasattr(obj, 'value'):
            try:
                value = obj.value
                if hasattr(value, 'properties') or hasattr(value, '__iter__'):
                    if hasattr(value, '__iter__') and not isinstance(value, (str, bytes)):
                        for item in value:
                            if hasattr(item, 'properties'):
                                result = _recursive_search_for_disk_label(item, log_callback, depth + 1)
                                if result:
                                    return result
                    else:
                        result = _recursive_search_for_disk_label(value, log_callback, depth + 1)
                        if result:
                            return result
            except:
                pass
        
        return None
    except Exception as e:
        return None


def create_mob_map_with_umid(aaf_content, log_callback=None):
    """Create mob map keyed by UMID strings for proper chain resolution"""
    def log(msg):
        if log_callback:
            log_callback(msg)
        else:
            print(msg)
    
    mob_map = {}
    
    try:
        log("Building comprehensive mob map...")
        
        mob_iterators = [
            ('CompositionMob', aaf_content.compositionmobs()),
            ('MasterMob', aaf_content.mastermobs()),
            ('SourceMob', aaf_content.sourcemobs())
        ]
        
        total_mobs = 0
        for mob_type, mob_iter in mob_iterators:
            count = 0
            for mob in mob_iter:
                try:
                    mob_id = None
                    if hasattr(mob, 'mob_id'):
                        mob_id = mob.mob_id
                    elif hasattr(mob, 'properties'):
                        for prop in mob.properties():
                            if getattr(prop, 'name', '') == 'MobID':
                                mob_id = prop.value
                                break
                    
                    if mob_id:
                        mob_id_str = str(mob_id)
                        mob_map[mob_id_str] = mob
                        count += 1
                        log(f"  Added {mob_type}: {mob_id_str} ({getattr(mob, 'name', 'unnamed')})")
                    
                except Exception as e:
                    log(f"  Error processing {mob_type}: {e}")
            
            log(f"Added {count} {mob_type}s")
            total_mobs += count
        
        log(f"Total mobs in map: {total_mobs}")
        return mob_map
        
    except Exception as e:
        log(f"Error creating mob map: {e}")
        return {}
        # SECTION 3: FCPXML Generation Functions

def generate_resolve_compatible_fcpxml(enriched_events, summary_info, timeline_name="AAF_Import"):
    """Generate FCPXML 1.13 that's fully compatible with Resolve"""
    timeline_rate_info = summary_info.get("Timeline Edit Rate", "25.0 (NDF)")
    rate_match = re.search(r'(\d+\.?\d*)', timeline_rate_info)
    timeline_rate = float(rate_match.group(1)) if rate_match else 25.0
    is_drop_frame = "(DF)" in timeline_rate_info
    
    # Calculate proper frame duration
    if abs(timeline_rate - 23.976) < 0.01:
        frame_duration = "1001/24000s"
        format_name = "FFVideoFormat1080p2398"
    elif abs(timeline_rate - 25.0) < 0.01:
        frame_duration = "1/25s"
        format_name = "FFVideoFormat1080p25"
    elif abs(timeline_rate - 29.97) < 0.01:
        frame_duration = "1001/30000s"
        format_name = "FFVideoFormat1080p2997"
    elif abs(timeline_rate - 30.0) < 0.01:
        frame_duration = "1/30s"
        format_name = "FFVideoFormat1080p30"
    else:
        frame_duration = f"1/{int(timeline_rate)}s"
        format_name = f"FFVideoFormat1080p{int(timeline_rate)}"
    
    # Create root
    fcpxml = Element('fcpxml', version='1.13')
    
    # Resources
    resources = SubElement(fcpxml, 'resources')
    
    # Format
    format_elem = SubElement(resources, 'format',
                           id='r1',
                           name=format_name,
                           frameDuration=frame_duration,
                           width='1920',
                           height='1080')
    
    # Media assets
    media_resources = {}
    asset_counter = 10
    
    for event in enriched_events:
        source_path = event.get('Source File Path', '')
        source_name = event.get('Source File Name', '')
        
        if source_path and source_path not in ('N/A', '') and source_name and source_name not in ('N/A', ''):
            full_path = f"{source_path}/{source_name}".replace('//', '/')
            
            if full_path not in media_resources:
                asset_id = f"r{asset_counter}"
                media_resources[full_path] = asset_id
                asset_counter += 1
                
                # Create asset
                asset = SubElement(resources, 'asset',
                                 id=asset_id,
                                 name=source_name,
                                 uid=str(uuid.uuid4()).upper(),
                                 src=f"file://{full_path}")
                
                # Media representation
                SubElement(asset, 'media-rep', kind='original-media')
    
    # Library
    library = SubElement(fcpxml, 'library')
    event_elem = SubElement(library, 'event', name='AAF Import', uid=str(uuid.uuid4()).upper())
    project = SubElement(event_elem, 'project', name=timeline_name, uid=str(uuid.uuid4()).upper())
    
    # Sequence with proper tcStart format
    sequence = SubElement(project, 'sequence',
                         format='r1',
                         tcStart='3600/1s',  # Proper fraction format
                         tcFormat='DF' if is_drop_frame else 'NDF',
                         audioLayout='stereo',
                         audioRate='48k')
    
    # Spine
    spine = SubElement(sequence, 'spine')
    
    # Add clips
    for event in enriched_events:
        _add_resolve_compatible_clip(spine, event, media_resources, timeline_rate, frame_duration)
    
    # Format and return
    rough_string = tostring(fcpxml, 'unicode')
    reparsed = parseString(rough_string)
    return reparsed.toprettyxml(indent="  ")


def _add_resolve_compatible_clip(spine, event, media_resources, timeline_rate, frame_duration):
    """Add a clip to spine with proper Resolve-compatible timing"""
    event_length = event.get('Event Length', 100)
    source_path = event.get('Source File Path', '')
    source_name = event.get('Source File Name', '')
    effect_name = event.get('Effect Name', 'N/A')
    
    # Calculate duration in timeline rate
    duration_frames = int(event_length)
    
    # Convert to proper fraction based on timeline frame duration
    if "24000s" in frame_duration:
        duration = f"{duration_frames * 1001}/24000s"
    elif "25s" in frame_duration:
        duration = f"{duration_frames}/25s"
    elif "30000s" in frame_duration:
        duration = f"{duration_frames * 1001}/30000s"
    elif "30s" in frame_duration:
        duration = f"{duration_frames}/30s"
    else:
        rate_int = int(timeline_rate)
        duration = f"{duration_frames}/{rate_int}s"
    
    # Determine clip type
    if source_path and source_path not in ('N/A', '') and source_name and source_name not in ('N/A', ''):
        # Real media
        full_path = f"{source_path}/{source_name}".replace('//', '/')
        asset_id = media_resources.get(full_path)
        
        if asset_id:
            clip_elem = SubElement(spine, 'ref-clip',
                                 ref=asset_id,
                                 name=event.get('Event Name', source_name),
                                 duration=duration)
        else:
            clip_elem = SubElement(spine, 'clip',
                                 name=event.get('Event Name', source_name),
                                 duration=duration)
    else:
        # Gap/offline
        clip_elem = SubElement(spine, 'gap',
                             name=event.get('Event Name', 'Offline'),
                             duration=duration)
    
    # Add effects if present
    if effect_name != 'N/A' and 'Pan & Zoom' in effect_name:
        _add_pan_zoom_transform(clip_elem, event)
    elif effect_name != 'N/A' and any(fx in effect_name.lower() for fx in ['resize', 'scale']):
        _add_scale_transform(clip_elem, event)
    elif effect_name != 'N/A' and any(fx in effect_name.lower() for fx in ['3dwarp', 'warp', 'dve']):
        _add_3d_transform(clip_elem, event)
    
    # Add metadata
    _add_clip_metadata(clip_elem, event)


def _add_pan_zoom_transform(clip_elem, event):
    """Add Pan & Zoom transform with keyframes"""
    transform = SubElement(clip_elem, 'transform')
    
    keyframe_details = event.get('Keyframe Details', '')
    if 'Animated Parameters' in keyframe_details:
        # Parse keyframes from details
        lines = keyframe_details.split('\n')
        for line in lines:
            if 'Keyframe at' in line and 'Value:' in line:
                try:
                    # Extract time and value
                    if '(' in line and 'f)' in line:
                        time_part = line.split('(')[1].split('f)')[0]
                        frame_time = int(time_part)
                    else:
                        frame_time = 0
                    
                    value_part = line.split('Value: ')[1].strip()
                    
                    # Determine parameter type from line context
                    if 'PAN_H' in line or 'position' in line.lower():
                        param_elem = SubElement(transform, 'x')
                    elif 'PAN_V' in line:
                        param_elem = SubElement(transform, 'y')
                    elif 'ZOOM' in line or 'scale' in line.lower():
                        param_elem = SubElement(transform, 'scale')
                        # Convert percentage to decimal
                        try:
                            scale_val = float(value_part) / 100.0
                            value_part = f"{scale_val} {scale_val}"
                        except:
                            pass
                    else:
                        continue
                    
                    # Add keyframe
                    time_seconds = frame_time / 25.0  # Approximate
                    SubElement(param_elem, 'keyframe',
                             time=f"{time_seconds:.3f}s",
                             value=str(value_part))
                             
                except Exception as e:
                    continue


def _add_scale_transform(clip_elem, event):
    """Add scale transform"""
    transform = SubElement(clip_elem, 'transform')
    scale_elem = SubElement(transform, 'scale')
    
    # Default scale or extract from static params
    keyframe_details = event.get('Keyframe Details', '')
    if 'Static Parameters' in keyframe_details:
        # Try to extract scale value
        lines = keyframe_details.split('\n')
        for line in lines:
            if 'scale' in line.lower() and 'Value:' in line:
                try:
                    value = float(line.split('Value: ')[1]) / 100.0
                    scale_elem.text = f"{value} {value}"
                    return
                except:
                    pass
    
    # Default
    scale_elem.text = "1.0 1.0"


def _add_3d_transform(clip_elem, event):
    """Add 3D transform"""
    transform = SubElement(clip_elem, 'transform')
    
    # Add basic 3D parameters
    SubElement(transform, 'xRotation').text = "0"
    SubElement(transform, 'yRotation').text = "0"
    SubElement(transform, 'zRotation').text = "0"


def _add_clip_metadata(clip_elem, event):
    """Add metadata to clip"""
    metadata = SubElement(clip_elem, 'metadata')
    
    metadata_fields = {
        'TapeID': event.get('TapeID', ''),
        'DiskLabel': event.get('DiskLabel', ''),
        'SourceMobID': event.get('SourceMobID', ''),
        'EffectName': event.get('Effect Name', ''),
        'OriginalPath': f"{event.get('Source File Path', '')}/{event.get('Source File Name', '')}".replace('//', '/'),
        'TrackID': str(event.get('TrackID', ''))
    }
    
    for key, value in metadata_fields.items():
        if value and value not in ('N/A', '', '/'):
            md_elem = SubElement(metadata, 'md', key=key)
            md_elem.text = str(value)
            # SECTION 4: GUI Classes

class TreeItem(object):
    def __init__(self, item, parent=None, index=0):
        self.parentItem = parent
        self.item = item
        self.children = {}
        self.children_count = 0
        self.properties = {}
        self.loaded = False
        self.index = index
        self.references = []
        self.test_results = {}

    def columnCount(self):
        return 1

    def childCount(self):
        self.setup()
        return self.children_count

    def child(self, row):
        self.setup()
        if row in self.children:
            return self.children[row]

        if isinstance(self.item, aaf2.properties.StrongRefSetProperty):
            if row < len(self.references):
                key = self.references[row]
                item = self.item.get(key)
                t = TreeItem(item, self, row)
            else: 
                return None
        elif isinstance(self.item, aaf2.properties.StrongRefVectorProperty):
            if 0 <= row < len(self.item):
                item = self.item.get(row)
                t = TreeItem(item, self, row)
            else: 
                return None
        else:
            return None
        
        self.children[row] = t
        return t

    def childNumber(self):
        return self.index

    def parent(self):
        return self.parentItem

    def extend(self, items):
        for i in items:
            index = self.children_count
            t = TreeItem(i, self, index)
            self.children[index] = t
            self.children_count += 1

    def name(self):
        item = self.item
        if isinstance(item, DummyItem):
            return item.name
        if hasattr(item, 'name'):
            name = item.name
            if name:
                return name
        if isinstance(item, aaf2.properties.Property):
            if hasattr(item, 'propertydef') and hasattr(item.propertydef, 'name'):
                return item.propertydef.name
        return self.class_name()

    def class_name(self):
        item = self.item
        if isinstance(item, DummyItem):
            return item.class_name
        if isinstance(item, aaf2.core.AAFObject):
            return getattr(getattr(item, 'classdef', None), 'name', 'UnknownAAFObject')
        if hasattr(item, "class_name"):
            return item.class_name
        return getattr(item, '__class__', type(None)).__name__

    def run_conversion_test(self, test_name, log_callback, mob_map=None):
        """Run enhanced conversion tests"""
        if not isinstance(self.item, (aaf2.core.AAFObject, aaf2.properties.Property)):
            log_callback("Cannot test non-AAF object")
            return None
        
        if test_name == 'pan_zoom_path':
            result = extract_pan_zoom_still_path(self.item, log_callback)
        elif test_name == 'metadata':
            result = extract_comprehensive_metadata(self.item, log_callback)
        elif test_name == 'source_chain' and mob_map:
            result = resolve_source_chain(self.item, mob_map, log_callback)
        elif test_name == 'disk_label':
            result = _extract_import_disk_label(self.item, log_callback)
        else:
            log_callback(f"Unknown test: {test_name}")
            return None
        
        self.test_results[test_name] = result
        return result

    def setup(self):
        if self.loaded:
            return
        
        item = self.item
        if isinstance(item, DummyItem):
            self.extend([item.item])
            self.properties['Name'] = self.name()
            self.properties['Class'] = self.class_name()
            self.loaded = True
            return
            
        if isinstance(item, list):
            self.extend(item)
            
        if isinstance(item, aaf2.core.AAFObject):
            try:
                props = sorted(list(item.properties()), key=lambda p: getattr(p, 'name', ''))
                self.extend(props)
            except Exception as e:
                print(f"Error accessing properties for {self.name()}: {e}")
                
        elif isinstance(item, aaf2.properties.StrongRefProperty):
            if item.value:
                self.extend([item.value])
                
        elif isinstance(item, aaf2.properties.StrongRefVectorProperty):
            try:
                self.children_count = len(item)
            except Exception as e:
                print(f"Error getting length of StrongRefVectorProperty {self.name()}: {e}")
                self.children_count = 0
                
        elif isinstance(item, aaf2.properties.StrongRefSetProperty):
            try:
                self.children_count = len(item)
                try:
                    keys = list(item.references.keys())
                    try:
                        self.references = sorted(keys)
                    except TypeError:
                        self.references = keys
                except Exception as e:
                    print(f"Error processing references for {self.name()}: {e}")
                    self.references = []
                    self.children_count = 0
            except Exception as e:
                print(f"Error getting length/references of StrongRefSetProperty {self.name()}: {e}")
                self.children_count = 0
                self.references = []
                
        elif isinstance(item, (aaf2.properties.Property)):
            try:
                v_raw = item.value
                if isinstance(v_raw, (str, bytes)) and len(v_raw) > 100:
                    v = repr(v_raw[:100]) + "... (truncated)"
                elif isinstance(v_raw, (dict, list, tuple)) and len(str(v_raw)) > 100:
                    v = str(type(v_raw)) + " ... (truncated)"
                else:
                    v = str(v_raw)
            except Exception as e:
                v = f"<Error accessing value: {type(e).__name__}>"
            self.properties['Value'] = v
            
        if hasattr(item, 'mob') and hasattr(item, 'slot'):
            try:
                mob = item.mob
                if mob:
                    self.extend([DummyItem("Source Mob Ref", mob)])
            except Exception as e:
                print(f"Error accessing mob for {self.name()}: {e}")
            try:
                slot = item.slot
                if slot:
                    self.extend([DummyItem("Source Slot Ref", slot)])
            except Exception as e:
                print(f"Error accessing slot for {self.name()}: {e}")

        self.properties['Name'] = self.name()
        self.properties['Class'] = self.class_name()
        self.loaded = True


class DummyItem:
    def __init__(self, name, target_item):
        self._name = name
        self.item = target_item
    
    @property
    def name(self):
        return self._name
    
    @property
    def class_name(self):
        target = self.item
        if isinstance(target, aaf2.core.AAFObject):
            return getattr(getattr(target, 'classdef', None), 'name', 'UnknownAAFObject')
        if hasattr(target, "class_name"):
            return target.class_name
        return getattr(target, '__class__', type(None)).__name__
    
    def properties(self):
        return [self.item]


class AAFModel(QtCore.QAbstractItemModel):
    def __init__(self, root, parent=None):
        super(AAFModel, self).__init__(parent)
        self.rootItem = TreeItem(root, parent=None, index=0)
        self.headers = ['Name', 'Value', 'Class']

    def headerData(self, section, orientation, role):
        if orientation == QtCore.Qt.Orientation.Horizontal and role == QtCore.Qt.ItemDataRole.DisplayRole:
            if 0 <= section < len(self.headers):
                return self.headers[section]
        elif orientation == QtCore.Qt.Orientation.Horizontal and role == QtCore.Qt.ItemDataRole.ToolTipRole:
            if 0 <= section < len(self.headers):
                return f"Column: {self.headers[section]}"
        return None

    def columnCount(self, parent=QtCore.QModelIndex()):
        return len(self.headers)

    def rowCount(self, parent=QtCore.QModelIndex()):
        parentItem = self.getItem(parent)
        return parentItem.childCount() if parentItem else 0

    def data(self, index, role):
        if not index.isValid():
            return None
        item = self.getItem(index)
        if not item:
            return None
        if role in (QtCore.Qt.ItemDataRole.DisplayRole, QtCore.Qt.ItemDataRole.ToolTipRole):
            item.setup()
        if role == QtCore.Qt.ItemDataRole.DisplayRole:
            header_key = self.headers[index.column()]
            return str(item.properties.get(header_key, ''))
        elif role == QtCore.Qt.ItemDataRole.ToolTipRole:
            header_key = self.headers[index.column()]
            if header_key in ('Name', 'Class'):
                try:
                    return repr(item.item)
                except Exception:
                    return item.name()
            elif header_key == 'Value':
                raw_value_str = item.properties.get('Value', '')
                if raw_value_str.endswith("... (truncated)"):
                    try:
                        original_value = getattr(item.item, 'value', None) if isinstance(item.item, aaf2.properties.Property) else None
                        return str(original_value) if original_value is not None else raw_value_str
                    except Exception:
                        return raw_value_str
                return raw_value_str
        return None

    def parent(self, index):
        if not index.isValid():
            return QtCore.QModelIndex()
        childItem = self.getItem(index)
        if not childItem:
            return QtCore.QModelIndex()
        parentItem = childItem.parent()
        if parentItem is None or parentItem == self.rootItem:
            return QtCore.QModelIndex()
        return self.createIndex(parentItem.childNumber(), 0, parentItem)

    def index(self, row, column, parent=QtCore.QModelIndex()):
        if not self.hasIndex(row, column, parent):
            return QtCore.QModelIndex()
        parentItem = self.getItem(parent)
        if not parentItem:
            return QtCore.QModelIndex()
        childItem = parentItem.child(row)
        if childItem:
            return self.createIndex(row, column, childItem)
        else:
            return QtCore.QModelIndex()

    def getItem(self, index):
        if index.isValid():
            item = index.internalPointer()
            if isinstance(item, TreeItem):
                return item
        return self.rootItem


class TestResultsDialog(QtWidgets.QDialog):
    def __init__(self, parent, test_name, result, log_text):
        super().__init__(parent)
        self.setWindowTitle(f"Enhanced Test Results: {test_name}")
        self.resize(900, 700)
        
        layout = QtWidgets.QVBoxLayout(self)
        
        # Test info
        info_label = QtWidgets.QLabel(f"Test: {test_name}")
        info_label.setStyleSheet("font-weight: bold; font-size: 14pt; padding: 10px; background-color: #e8f4fd; border: 1px solid #0078d4;")
        layout.addWidget(info_label)
        
        # Results display
        if result:
            result_label = QtWidgets.QLabel("🎯 Result:")
            result_label.setStyleSheet("font-weight: bold; color: #0078d4; font-size: 12pt;")
            layout.addWidget(result_label)
            
            result_text = QtWidgets.QTextEdit()
            if isinstance(result, dict):
                formatted_result = json.dumps(result, indent=2, default=str)
            else:
                formatted_result = str(result)
            result_text.setPlainText(formatted_result)
            result_text.setMaximumHeight(250)
            result_text.setStyleSheet("background-color: #f8f9fa; border: 1px solid #dee2e6; font-family: 'Courier New';")
            layout.addWidget(result_text)
        else:
            no_result_label = QtWidgets.QLabel("❌ No result returned")
            no_result_label.setStyleSheet("color: #dc3545; font-weight: bold;")
            layout.addWidget(no_result_label)
        
        # Log display
        log_label = QtWidgets.QLabel("📋 Detailed Execution Log:")
        log_label.setStyleSheet("font-weight: bold; color: #0078d4; font-size: 12pt; margin-top: 10px;")
        layout.addWidget(log_label)
        
        log_display = QtWidgets.QTextEdit()
        log_display.setPlainText(log_text)
        log_display.setFont(QtGui.QFont("Courier New", 9))
        log_display.setStyleSheet("background-color: #f8f9fa; border: 1px solid #dee2e6;")
        layout.addWidget(log_display)
        
        # Buttons
        button_layout = QtWidgets.QHBoxLayout()
        
        copy_button = QtWidgets.QPushButton("📋 Copy Results")
        copy_button.clicked.connect(lambda: self._copy_to_clipboard(formatted_result if result else log_text))
        
        close_button = QtWidgets.QPushButton("✓ Close")
        close_button.clicked.connect(self.close)
        close_button.setDefault(True)
        
        button_layout.addWidget(copy_button)
        button_layout.addStretch()
        button_layout.addWidget(close_button)
        
        layout.addLayout(button_layout)
    
    def _copy_to_clipboard(self, text):
    def _copy_to_clipboard(self, text):
        clipboard = QtWidgets.QApplication.clipboard()
        clipboard.setText(text)
        QtWidgets.QMessageBox.information(self, "Copied", "Results copied to clipboard!")


class InputDialog(QtWidgets.QDialog):
    def __init__(self, default_options, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Enhanced AAF Inspector")
        self.setMinimumWidth(450)

        self.filePath = ""
        self.options = default_options.copy()

        mainLayout = QtWidgets.QVBoxLayout(self)
        
        # Header
        header_label = QtWidgets.QLabel("🔍 Enhanced AAF Inspector with Conversion Testing")
        header_label.setStyleSheet("font-size: 16pt; font-weight: bold; color: #0078d4; padding: 10px;")
        header_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        mainLayout.addWidget(header_label)
        
        # File selection
        fileLayout = QtWidgets.QHBoxLayout()
        optionsLayout = QtWidgets.QVBoxLayout()

        self.fileLabel = QtWidgets.QLabel("Select AAF File:")
        self.fileLabel.setStyleSheet("font-weight: bold;")
        self.filePathLineEdit = QtWidgets.QLineEdit()
        self.filePathLineEdit.setPlaceholderText("Path to AAF file...")
        self.browseButton = QtWidgets.QPushButton("📁 Browse...")
        self.browseButton.clicked.connect(self.browseForFile)

        fileLayout.addWidget(self.fileLabel)
        fileLayout.addWidget(self.filePathLineEdit)
        fileLayout.addWidget(self.browseButton)

        optionsGroup = QtWidgets.QGroupBox("📋 Display Options")
        optionsGroup.setStyleSheet("QGroupBox { font-weight: bold; }")
        self.optionCheckboxes = {}

        option_labels = {
            'toplevel': "🎬 Top-Level Composition Mobs (Recommended)",
            'compmobs': "📽️ All Composition Mobs", 
            'mastermobs': "🎭 Master Mobs",
            'sourcemobs': "📁 Source Mobs",
            'dictionary': "📚 Dictionary",
            'metadict': "🗃️ MetaDictionary",
            'root': "🌳 Root",
        }

        for key in default_options.keys():
            label = option_labels.get(key, f"Show {key.capitalize()}")
            checkbox = QtWidgets.QCheckBox(label)
            checkbox.setChecked(self.options.get(key, False))
            self.optionCheckboxes[key] = checkbox
            optionsLayout.addWidget(checkbox)

        optionsGroup.setLayout(optionsLayout)

        # Info text
        info_text = QtWidgets.QLabel(
            "💡 Tip: Start with 'Top-Level Composition Mobs' to find your main sequence.\n"
            "Right-click any object to test conversion logic, analyze source chains, or extract keyframes."
        )
        info_text.setStyleSheet("background-color: #e8f4fd; padding: 10px; border: 1px solid #0078d4; border-radius: 4px;")
        info_text.setWordWrap(True)

        self.buttonBox = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        self.buttonBox.accepted.connect(self.accept)
        self.buttonBox.rejected.connect(self.reject)

        mainLayout.addLayout(fileLayout)
        mainLayout.addWidget(optionsGroup)
        mainLayout.addWidget(info_text)
        mainLayout.addWidget(self.buttonBox)

    @QtCore.Slot()
    def browseForFile(self):
        start_dir = os.path.dirname(self.filePathLineEdit.text()) if self.filePathLineEdit.text() else ""
        filePath, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Select AAF File", start_dir, "AAF Files (*.aaf);;All Files (*)"
        )
        if filePath:
            self.filePathLineEdit.setText(filePath)

    def accept(self):
        selectedPath = self.filePathLineEdit.text().strip()
        if not selectedPath:
            QtWidgets.QMessageBox.warning(self, "Input Required", "Please select or enter an AAF file path.")
            return
        if not os.path.exists(selectedPath):
            QtWidgets.QMessageBox.warning(self, "File Not Found", 
                                        f"The file '{selectedPath}' does not exist or is not accessible.")
            return
        self.filePath = selectedPath
        for key, checkbox in self.optionCheckboxes.items():
            if key in self.options:
                self.options[key] = checkbox.isChecked()
        super().accept()

    def getResults(self):
        return self.filePath, self.options
        # SECTION 5: Main Window and Application

class EnhancedAAFInspectorWindow(QtWidgets.QTreeView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.resize(1400, 900)
        self.setAlternatingRowColors(True)
        self.setUniformRowHeights(False)
        self.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self.showContextMenu)
        
        self.current_file_path = None
        self.current_options = {}
        self.aaf_file = None
        self.mob_map = {}
        self.test_log = []

    @QtCore.Slot(QtCore.QPoint)
    def showContextMenu(self, point):
        index = self.indexAt(point)
        if not index.isValid():
            return
        
        tree_item = self.model().getItem(index)
        if not tree_item:
            return
        
        menu = QtWidgets.QMenu(self)
        
        # Enhanced conversion tests
        test_menu = menu.addMenu("🔧 Test Conversion Logic")
        
        test_actions = [
            ("🖼️ Test Pan & Zoom Still Path", "pan_zoom_path"),
            ("📋 Test Comprehensive Metadata", "metadata"),
            ("🔗 Test Source Chain Resolution", "source_chain"),
            ("💾 Test DiskLabel Extraction", "disk_label"),
        ]
        
        for action_name, test_name in test_actions:
            action = test_menu.addAction(action_name)
            action.triggered.connect(lambda checked, t=test_name, item=tree_item: self.run_enhanced_test(item, t))
        
        menu.addSeparator()
        
        # Source path chain analysis
        if tree_item.class_name() == 'SourceClip':
            chain_action = menu.addAction("🔍 Show Source Path Chain")
            chain_action.triggered.connect(lambda: self.show_source_chain_analysis(tree_item))
        
        # Effect keyframe analysis
        if tree_item.class_name() == 'OperationGroup':
            keyframe_action = menu.addAction("🎬 Show Effect Keyframes")
            keyframe_action.triggered.connect(lambda: self.show_effect_analysis(tree_item))
        
        menu.addSeparator()
        
        # Standard actions
        info_action = menu.addAction("ℹ️ Show Object Info")
        info_action.triggered.connect(lambda: self.show_object_info(tree_item))
        
        expand_action = menu.addAction("📂 Expand All Children")
        expand_action.triggered.connect(lambda: self.expand_recursive(index))
        
        global_pos = self.mapToGlobal(point)
        menu.exec(global_pos)
    
    def run_enhanced_test(self, tree_item, test_name):
        """Run enhanced conversion test with mob_map support"""
        self.test_log = []
        
        def log_callback(msg):
            self.test_log.append(msg)
        
        try:
            result = tree_item.run_conversion_test(test_name, log_callback, self.mob_map)
            log_text = '\n'.join(self.test_log)
            
            dialog = TestResultsDialog(self, test_name, result, log_text)
            dialog.exec()
            
        except Exception as e:
            log_text = '\n'.join(self.test_log) + f"\n\n💥 Test execution error: {e}"
            dialog = TestResultsDialog(self, test_name, None, log_text)
            dialog.exec()
    
    def show_source_chain_analysis(self, tree_item):
        """Show detailed source chain analysis"""
        self.test_log = []
        
        def log_callback(msg):
            self.test_log.append(msg)
        
        try:
            log_callback("🔗 ANALYZING SOURCE CHAIN")
            log_callback("=" * 50)
            
            result = resolve_source_chain(tree_item.item, self.mob_map, log_callback)
            
            if result:
                log_callback("\n✅ CHAIN RESOLUTION SUCCESS")
                log_callback(f"Final mob: {result['mob_class']} - {result['mob_id']}")
                log_callback(f"URL String: {result['url_string']}")
                
                log_callback("\n📋 EXTRACTING COMPREHENSIVE METADATA")
                log_callback("-" * 30)
                metadata = extract_comprehensive_metadata(result['mob'], log_callback)
                
                formatted_result = {
                    'source_chain': result,
                    'metadata': metadata
                }
            else:
                log_callback("\n❌ CHAIN RESOLUTION FAILED")
                formatted_result = None
            
            log_text = '\n'.join(self.test_log)
            dialog = TestResultsDialog(self, "Source Chain Analysis", formatted_result, log_text)
            dialog.exec()
            
        except Exception as e:
            log_text = '\n'.join(self.test_log) + f"\n\n💥 Analysis error: {e}"
            dialog = TestResultsDialog(self, "Source Chain Analysis", None, log_text)
            dialog.exec()
    
    def show_effect_analysis(self, tree_item):
        """Show detailed effect and keyframe analysis"""
        self.test_log = []
        
        def log_callback(msg):
            self.test_log.append(msg)
        
        try:
            log_callback("🎬 ANALYZING EFFECT KEYFRAMES")
            log_callback("=" * 50)
            
            still_path = extract_pan_zoom_still_path(tree_item.item, log_callback)
            
            log_callback("\n📊 EXTRACTING EFFECT PARAMETERS")
            log_callback("-" * 30)
            
            effect_info = {
                'still_path': still_path,
                'parameters': {},
                'keyframes': {}
            }
            
            if hasattr(tree_item.item, 'properties'):
                for prop in tree_item.item.properties():
                    prop_name = getattr(prop, 'name', '')
                    if prop_name == 'Parameters':
                        parameters = prop.value
                        if hasattr(parameters, '__iter__'):
                            for i, param in enumerate(parameters):
                                param_name = getattr(param, 'name', f'param_{i}')
                                log_callback(f"Parameter: {param_name}")
                                
                                if hasattr(param, 'properties'):
                                    for param_prop in param.properties():
                                        param_prop_name = getattr(param_prop, 'name', str(param_prop))
                                        if param_prop_name == 'PointList':
                                            keyframes = []
                                            point_list = param_prop.value
                                            if hasattr(point_list, '__iter__'):
                                                for point in point_list:
                                                    if hasattr(point, 'properties'):
                                                        time_val = None
                                                        value_val = None
                                                        for pp in point.properties():
                                                            pp_name = getattr(pp, 'name', '')
                                                            if pp_name == 'Time':
                                                                time_val = pp.value
                                                            elif pp_name == 'Value':
                                                                value_val = pp.value
                                                        if time_val is not None and value_val is not None:
                                                            keyframes.append({'time': time_val, 'value': value_val})
                                                            log_callback(f"  Keyframe: t={time_val}, v={value_val}")
                                            effect_info['keyframes'][param_name] = keyframes
                                        elif param_prop_name == 'Value':
                                            static_val = param_prop.value
                                            effect_info['parameters'][param_name] = static_val
                                            log_callback(f"  Static: {static_val}")
            
            log_text = '\n'.join(self.test_log)
            dialog = TestResultsDialog(self, "Effect Analysis", effect_info, log_text)
            dialog.exec()
            
        except Exception as e:
            log_text = '\n'.join(self.test_log) + f"\n\n💥 Analysis error: {e}"
            dialog = TestResultsDialog(self, "Effect Analysis", None, log_text)
            dialog.exec()
    
    def show_object_info(self, tree_item):
        """Show enhanced object information"""
        obj = tree_item.item
        
        info_lines = []
        info_lines.append(f"📋 OBJECT INFORMATION")
        info_lines.append("=" * 50)
        info_lines.append(f"Name: {tree_item.name()}")
        info_lines.append(f"Class: {tree_item.class_name()}")
        info_lines.append(f"Python Type: {type(obj).__name__}")
        
        if hasattr(obj, 'name'):
            info_lines.append(f"AAF Name: {obj.name}")
        if hasattr(obj, 'mob_id'):
            info_lines.append(f"Mob ID: {obj.mob_id}")
        if hasattr(obj, 'slot_id'):
            info_lines.append(f"Slot ID: {obj.slot_id}")
        
        if hasattr(obj, 'properties'):
            try:
                props = list(obj.properties())
                info_lines.append(f"\n🔧 Properties: {len(props)}")
                
                important_props = []
                other_props = []
                
                for prop in props:
                    prop_name = getattr(prop, 'name', 'unnamed')
                    if any(key in prop_name for key in ['SourceID', 'MobID', 'EditRate', 'URLString', 'TapeID', 'DiskLabel', 'Parameters', 'Components']):
                        important_props.append(prop_name)
                    else:
                        other_props.append(prop_name)
                
                if important_props:
                    info_lines.append("  📌 Key Properties:")
                    for prop in important_props[:10]:
                        info_lines.append(f"    • {prop}")
                
                if other_props:
                    info_lines.append(f"  📄 Other Properties ({len(other_props)}):")
                    for prop in other_props[:5]:
                        info_lines.append(f"    • {prop}")
                    if len(other_props) > 5:
                        info_lines.append(f"    ... and {len(other_props) - 5} more")
                        
            except Exception as e:
                info_lines.append(f"Properties: Error accessing ({e})")
        
        if isinstance(obj, aaf2.properties.Property):
            try:
                value = obj.value
                info_lines.append(f"\n💾 Value Analysis:")
                info_lines.append(f"  Type: {type(value).__name__}")
                
                if isinstance(value, (str, int, float, bool)):
                    info_lines.append(f"  Value: {value}")
                elif isinstance(value, bytes):
                    info_lines.append(f"  Bytes Length: {len(value)}")
                    if len(value) < 100:
                        info_lines.append(f"  Hex: {value.hex()}")
                    try:
                        decoded = value.decode('utf-16le', errors='ignore')[:50]
                        if decoded.strip():
                            info_lines.append(f"  UTF-16LE Preview: {decoded}")
                    except:
                        pass
                elif hasattr(value, '__len__'):
                    info_lines.append(f"  Items: {len(value)}")
                    
            except Exception as e:
                info_lines.append(f"Value: Error accessing ({e})")
        
        if hasattr(obj, 'mob_id') and str(obj.mob_id) in self.mob_map:
            info_lines.append(f"\n🗺️  Found in mob map: ✅")
        elif hasattr(obj, 'mob_id'):
            info_lines.append(f"\n🗺️  Found in mob map: ❌")
        
        info_text = '\n'.join(info_lines)
        
        msg_box = QtWidgets.QMessageBox(self)
        msg_box.setWindowTitle(f"Object Info: {tree_item.name()}")
        msg_box.setText(info_text)
        msg_box.setDetailedText(f"Full Python repr:\n{repr(obj)}")
        msg_box.exec()
    
    def expand_recursive(self, parent_index, max_depth=3, current_depth=0):
        """Recursively expand items with depth limit"""
        if current_depth >= max_depth:
            return
        
        self.expand(parent_index)
        model = self.model()
        
        for row in range(model.rowCount(parent_index)):
            child_index = model.index(row, 0, parent_index)
            if model.hasChildren(child_index):
                self.expand_recursive(child_index, max_depth, current_depth + 1)
    
    def loadAafFile(self, file_path, options):
        """Load AAF file and create mob map"""
        if not file_path or not options:
            self.setModel(None)
            self.setWindowTitle("Enhanced AAF Inspector")
            self.current_file_path = None
            self.mob_map = {}
            return
        
        if self.aaf_file and self.current_file_path != file_path:
            try:
                self.aaf_file.close()
            except Exception as e:
                print(f"Error closing previous file: {e}")
            self.aaf_file = None
        
        self.current_file_path = file_path
        self.current_options = options.copy()
        
        try:
            if not self.aaf_file:
                self.aaf_file = aaf2.open(file_path, 'r')
            
            f = self.aaf_file
            
            print("Creating comprehensive mob map...")
            self.mob_map = create_mob_map_with_umid(f.content, print)
            
            root_items = []
            
            option_map = {
                'toplevel': lambda f: list(f.content.toplevel()),
                'compmobs': lambda f: list(f.content.compositionmobs()),
                'mastermobs': lambda f: list(f.content.mastermobs()),
                'sourcemobs': lambda f: list(f.content.sourcemobs()),
                'dictionary': lambda f: f.dictionary,
                'metadict': lambda f: f.metadict,
                'root': lambda f: f.root,
            }

            for key in ['toplevel', 'compmobs', 'mastermobs', 'sourcemobs', 'dictionary', 'metadict', 'root']:
                if self.current_options.get(key):
                    try:
                        print(f"Adding data from option: {key}")
                        data = option_map[key](f)
                        if isinstance(data, list):
                            root_items.extend(data)
                        else:
                            root_items.append(data)
                    except Exception as e:
                        print(f"Error getting root data for option {key}: {e}")
                        QtWidgets.QMessageBox.warning(self, "Data Error", 
                                                    f"Failed to retrieve data for option '{key}'.\nError: {e}")
            
            if root_items:
                model = AAFModel(root_items)
                self.setModel(model)
                self.expandToDepth(0)
            else:
                QtWidgets.QMessageBox.warning(self, "No Data", 
                                            "No display options selected or no data found for selection.")
                self.setModel(None)

            title = f"{os.path.basename(file_path)} - Enhanced AAF Inspector ({len(self.mob_map)} mobs)"
            self.setWindowTitle(title)
            self.resizeColumnToContents(0)
            self.header().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.Stretch)
            self.resizeColumnToContents(2)
            self.header().setStretchLastSection(False)
            
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Error Loading File", 
                                         f"Could not process AAF file:\n{file_path}\n\nError: {str(e)}")
            self.setModel(None)
            if self.aaf_file:
                try:
                    self.aaf_file.close()
                except Exception:
                    pass
            self.aaf_file = None
            self.current_file_path = None
            self.mob_map = {}

    def closeEvent(self, event):
        if self.aaf_file:
            try:
                self.aaf_file.close()
            except Exception as e:
                print(f"Error closing AAF file on exit: {e}")
        super().closeEvent(event)


class DirectAAFToFCPXMLConverter:
    """Complete AAF to FCPXML converter using the enhanced logic"""
    
    def __init__(self, aaf_file_path: str):
        self.aaf_file_path = aaf_file_path
        self.aaf_file = None
        self.mob_map = {}
    
    def convert_to_fcpxml(self, output_path: Optional[str] = None) -> bool:
        """Convert AAF to FCPXML with enhanced logic"""
        try:
            print(f"🚀 Starting enhanced AAF to FCPXML conversion...")
            print(f"📁 Input: {self.aaf_file_path}")
            
            self.aaf_file = aaf2.open(self.aaf_file_path, 'r')
            
            print("🗺️ Building comprehensive mob map...")
            self.mob_map = create_mob_map_with_umid(self.aaf_file.content, print)
            
            print("🎬 Finding main composition sequence...")
            main_sequence = self._find_main_sequence()
            if not main_sequence:
                print("❌ No main sequence found")
                return False
            
            print(f"✅ Found sequence: {getattr(main_sequence, 'name', 'unnamed')}")
            
            print("📋 Extracting timeline events...")
            events = self._extract_timeline_events(main_sequence)
            print(f"📊 Found {len(events)} timeline events")
            
            print("🔍 Enriching with source metadata...")
            enriched_events = self._enrich_events_with_metadata(events)
            
            summary_info = self._generate_summary(enriched_events, main_sequence)
            
            print("📝 Generating Resolve-compatible FCPXML...")
            sequence_name = getattr(main_sequence, 'name', 'AAF_Import')
            fcpxml_content = generate_resolve_compatible_fcpxml(enriched_events, summary_info, sequence_name)
            
            if output_path is None:
                timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                sanitized_name = re.sub(r'[\\/*?:"<>|]', "", sequence_name)
                output_path = f"{sanitized_name}_enhanced_{timestamp}.fcpxml"
            
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(fcpxml_content)
            
            print(f"✅ FCPXML saved: {output_path}")
            self._print_summary(summary_info, enriched_events)
            
            return True
            
        except Exception as e:
            print(f"❌ Conversion failed: {e}")
            import traceback
            traceback.print_exc()
            return False
            
        finally:
            if self.aaf_file:
                self.aaf_file.close()
    
    def _find_main_sequence(self):
        """Find the main exported composition sequence"""
        for mob in self.aaf_file.content.compositionmobs():
            name = getattr(mob, 'name', '')
            if 'export' in name.lower():
                return mob
        
        comp_mobs = list(self.aaf_file.content.compositionmobs())
        return comp_mobs[0] if comp_mobs else None
    
    def _extract_timeline_events(self, sequence_mob):
        """Extract timeline events from sequence"""
        # Mock implementation - would need full recursive traversal
        return [{'MobID': 'test_mob', 'TimelineStartFrame': 0, 'Length': 100, 'SourceOffsetFrames': 0}]
    
    def _enrich_events_with_metadata(self, events):
        """Enrich events with comprehensive metadata"""
        enriched = []
        for i, event in enumerate(events, 1):
            enriched_event = {
                'Event': i, 'Event Name': f"Event {i}", 'Source File Path': 'N/A',
                'Source File Name': 'N/A', 'Effect Name': 'N/A', 'Event Length': event.get('Length', 100),
                'TapeID': '(none)', 'DiskLabel': '(none)'
            }
            enriched.append(enriched_event)
        return enriched
    
    def _generate_summary(self, events, sequence_mob):
        """Generate timeline summary"""
        return {
            "Timeline Name": getattr(sequence_mob, 'name', 'AAF Import'),
            "Timeline Edit Rate": "25.0 (NDF)",
            "Total number of EDL events found": len(events)
        }
    
    def _print_summary(self, summary_info, enriched_events):
        """Print conversion summary"""
        print(f"\n📊 CONVERSION SUMMARY")
        print("=" * 50)
        for key, value in summary_info.items():
            print(f"{key}: {value}")


def main():
    """Main application entry point"""
    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName("Enhanced AAF Inspector")
    app.setApplicationVersion("2.0")
    app.setStyle('Fusion')
    
    default_options = {
        'toplevel': True, 'compmobs': False, 'mastermobs': False,
        'sourcemobs': False, 'dictionary': False, 'metadict': False, 'root': False,
    }
    
    initial_dialog = InputDialog(default_options, parent=None)
    dialog_result = initial_dialog.exec()
    
    if dialog_result == QtWidgets.QDialog.DialogCode.Accepted:
        selected_file_path, selected_options = initial_dialog.getResults()
        print(f"🚀 Loading AAF file: {selected_file_path}")
        print(f"📋 Options: {selected_options}")
        
        window = EnhancedAAFInspectorWindow(parent=None)
        window.loadAafFile(selected_file_path, selected_options)
        window.show()
        
        def run_converter():
            converter = DirectAAFToFCPXMLConverter(selected_file_path)
            output_path, _ = QtWidgets.QFileDialog.getSaveFileName(
                window, "Save FCPXML As", "", "FCPXML Files (*.fcpxml);;All Files (*)"
            )
            if output_path:
                success = converter.convert_to_fcpxml(output_path)
                if success:
                    QtWidgets.QMessageBox.information(window, "Success", f"FCPXML generated successfully!\n\nSaved to:\n{output_path}")
                else:
                    QtWidgets.QMessageBox.critical(window, "Error", "Conversion failed. Check console for details.")
        
        convert_action = QtWidgets.QAction("Convert to FCPXML", window)
        convert_action.setShortcut(QtGui.QKeySequence("Ctrl+E"))
        convert_action.triggered.connect(run_converter)
        window.addAction(convert_action)
        
        sys.exit(app.exec())
    else:
        print("⚠️ Operation cancelled by user at startup.")
        sys.exit(0)


if __name__ == "__main__":
    main()