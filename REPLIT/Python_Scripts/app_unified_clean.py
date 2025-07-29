#!/usr/bin/env python3
"""
Clean Unified AAF to DaVinci Resolve XML Converter
Fixed display logic and download functionality
"""

import streamlit as st
import json
import zipfile
import io
import os
import tempfile
import traceback
from datetime import datetime
from unified_aaf_parser import parse_aaf_unified
from resolve_xml_generator_v7 import ResolveXMLGeneratorV7

# Page configuration
st.set_page_config(
    page_title="Clean AAF to DaVinci Resolve XML Converter",
    page_icon="🎬",
    layout="wide"
)

def create_complete_package(xml_content, debug_content, aaf_data, base_name):
    """Create comprehensive package with all files"""
    package_buffer = io.BytesIO()
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    
    with zipfile.ZipFile(package_buffer, 'w', zipfile.ZIP_DEFLATED) as zipf:
        # Add XML output
        zipf.writestr(f'XML_Outputs/{base_name}_V6_COMPLETE.fcpxml', xml_content)
        
        # Add debug information
        zipf.writestr(f'Debug_Files/{base_name}_debug_{timestamp}.txt', debug_content)
        
        # Add JSON intermediate data
        json_data = json.dumps(aaf_data, indent=2, default=str)
        zipf.writestr(f'JSON_Data/{base_name}_aaf_structure.json', json_data)
        
        # Add all active Python scripts
        python_files = [
            'app_unified_clean.py',
            'unified_aaf_parser.py', 
            'resolve_xml_generator_v7.py',
            'json_aaf_parser_proven.py'
        ]
        
        for py_file in python_files:
            if os.path.exists(py_file):
                with open(py_file, 'r') as f:
                    content = f.read()
                zipf.writestr(f'Python_Scripts/{py_file}', content)
        
        # Add package manifest
        manifest = f"""# Complete Package Manifest
Generated: {datetime.now().isoformat()}

## Contents:
- XML_Outputs: Final FCPXML with V6 field mapping
- Debug_Files: Complete debug information
- JSON_Data: Intermediate AAF structure data  
- Python_Scripts: All active converter modules

## Features:
- Complete keyframe extraction from AAF
- Field-for-field JSON to XML mapping
- DaVinci Resolve compatibility
- Still image support
- Proper timeline structure

Package created: {timestamp}
Base name: {base_name}
"""
        zipf.writestr('PACKAGE_MANIFEST.md', manifest)
    
    package_buffer.seek(0)
    return package_buffer.getvalue()

def main():
    """Main application interface"""
    st.title("🎬 Clean AAF to DaVinci Resolve XML Converter")
    st.markdown("Convert AAF files to DaVinci Resolve-compatible FCPXML with proper keyframe extraction")
    
    # Reset and test buttons
    col1, col2, col3 = st.columns([2, 1, 1])
    with col1:
        st.write("")
    with col2:
        if st.button("🔄 Reset", type="secondary", help="Clear all results to start fresh"):
            # Clear session state
            for key in list(st.session_state.keys()):
                del st.session_state[key]
            st.rerun()
    with col3:
        if st.button("🧪 Test with Sample AAF", type="secondary"):
            st.session_state.test_mode = True
    
    # File upload section
    uploaded_file = st.file_uploader(
        "Upload your AAF file",
        type=['aaf'],
        help="Supports AAF files from Avid Media Composer and DaVinci Resolve"
    )
    
    # Handle test mode
    if st.session_state.get('test_mode', False):
        st.info("🧪 Using sample AAF file for testing")
        sample_aaf_path = 'attached_assets/BLINK_WORLD_WW2D EP1 SEQ TEST_1753566023371.aaf'
        if os.path.exists(sample_aaf_path):
            # Create a mock uploaded file object for the sample
            class MockUploadedFile:
                def __init__(self, file_path, name):
                    self.file_path = file_path
                    self.name = name
                
                def read(self):
                    with open(self.file_path, 'rb') as f:
                        return f.read()
            
            uploaded_file = MockUploadedFile(sample_aaf_path, 'BLINK_WORLD_WW2D EP1 SEQ TEST_1753566023371.aaf')
            st.session_state.test_mode = False
        else:
            st.error("Sample AAF file not found")
            st.session_state.test_mode = False
            uploaded_file = None
    
    if uploaded_file is not None:
        try:
            # Save uploaded file temporarily
            with tempfile.NamedTemporaryFile(delete=False, suffix='.aaf') as temp_file:
                temp_file.write(uploaded_file.read())
                temp_path = temp_file.name
            
            base_name = uploaded_file.name.replace('.aaf', '')
            
            with st.spinner("Parsing AAF file..."):
                # Parse AAF file
                aaf_data = parse_aaf_unified(temp_path)
                
                clips = aaf_data.get('clips', [])
                effects = aaf_data.get('effects', [])
                filler_effects = aaf_data.get('filler_effects', [])
                composition_info = aaf_data.get('composition_info', {})
                composition_name = composition_info.get('name', 'Unknown Sequence')
                source_app = aaf_data.get('source_application', 'unknown')
                
                # Calculate accurate keyframe statistics
                clips_with_keyframes = 0
                total_keyframes = 0
                for clip in clips:
                    keyframe_data = clip.get('keyframe_data', {})
                    if keyframe_data:
                        clips_with_keyframes += 1
                        for param_list in keyframe_data.values():
                            if isinstance(param_list, list):
                                total_keyframes += len(param_list)
            
            with st.spinner("Generating FCPXML..."):
                # Generate XML using V7 generator (improved version)
                generator = ResolveXMLGeneratorV7()
                xml_content = generator.generate_xml(aaf_data)
                
                # Create debug information
                debug_lines = [
                    f"AAF to FCPXML Conversion Report",
                    f"Generated: {datetime.now().isoformat()}",
                    f"Source File: {uploaded_file.name}",
                    f"Source Application: {source_app}",
                    f"",
                    f"STATISTICS:",
                    f"- Total clips: {len(clips)}",
                    f"- Clips with keyframes: {clips_with_keyframes}",
                    f"- Total keyframes: {total_keyframes}",
                    f"- Filler effects: {len(filler_effects)}",
                    f"- Sequence: {composition_name}",
                    f"",
                    f"CLIP DETAILS:"
                ]
                
                for i, clip in enumerate(clips):
                    source_name = clip.get('source_name', f'Clip_{i}')
                    timeline_start = clip.get('timeline_start_tc', 'Unknown')
                    keyframe_data = clip.get('keyframe_data', {})
                    
                    debug_lines.append(f"  {i+1}. {source_name}")
                    debug_lines.append(f"     Timeline: {timeline_start}")
                    
                    if keyframe_data:
                        kf_count = sum(len(v) if isinstance(v, list) else 0 for v in keyframe_data.values())
                        debug_lines.append(f"     Keyframes: {kf_count}")
                        debug_lines.append(f"     Parameters: {list(keyframe_data.keys())[:3]}")
                    else:
                        debug_lines.append(f"     Keyframes: None")
                    debug_lines.append("")
                
                debug_content = '\n'.join(debug_lines)
            
            # Show results
            st.success("✅ Conversion completed successfully!")
            
            # Display accurate statistics
            col1, col2 = st.columns(2)
            
            with col1:
                st.write("**📊 Conversion Statistics:**")
                st.write(f"- Source: {source_app.title()}")
                st.write(f"- Clips Found: {len(clips)}")
                st.write(f"- Clips with Animation: {clips_with_keyframes}")
                st.write(f"- Total Keyframes: {total_keyframes}")
                st.write(f"- Filler Effects: {len(filler_effects)}")
                st.write(f"- Sequence: {composition_name}")
            
            with col2:
                st.write("**📁 Download Options:**")
                
                # Individual downloads
                st.download_button(
                    label="📄 Download FCPXML",
                    data=xml_content,
                    file_name=f"{base_name}_V7_COMPLETE.fcpxml",
                    mime="application/xml",
                    help="Final FCPXML for DaVinci Resolve import (V7 Enhanced)"
                )
                
                st.download_button(
                    label="📊 Download JSON Data",
                    data=json.dumps(aaf_data, indent=2, default=str),
                    file_name=f"{base_name}_aaf_data.json",
                    mime="application/json",
                    help="Raw AAF data in JSON format"
                )
                
                st.download_button(
                    label="📝 Download Debug Info",
                    data=debug_content,
                    file_name=f"{base_name}_debug_info.txt",
                    mime="text/plain",
                    help="Detailed conversion information"
                )
                
                # Complete package download
                package_content = create_complete_package(xml_content, debug_content, aaf_data, base_name)
                st.download_button(
                    label="📦 Download Complete Package",
                    data=package_content,
                    file_name=f"{base_name}_COMPLETE_PACKAGE.zip",
                    mime="application/zip",
                    help="All files: FCPXML, JSON, debug, and Python scripts"
                )
            
            # Show sample clips with keyframes
            if clips_with_keyframes > 0:
                st.subheader("🎯 Animated Clips Found")
                with st.expander(f"View {clips_with_keyframes} clips with keyframes", expanded=False):
                    for clip in clips:
                        keyframe_data = clip.get('keyframe_data', {})
                        if keyframe_data:
                            source_name = clip.get('source_name', 'Unknown')
                            kf_count = sum(len(v) if isinstance(v, list) else 0 for v in keyframe_data.values())
                            st.write(f"**{source_name}:** {kf_count} keyframes")
                            st.write(f"  Parameters: {', '.join(list(keyframe_data.keys())[:5])}")
            
            # Show XML preview
            st.subheader("🔍 XML Preview")
            with st.expander("View generated FCPXML structure", expanded=False):
                xml_lines = xml_content.split('\n')
                preview_lines = xml_lines[:30]
                if len(xml_lines) > 30:
                    preview_lines.append("... (truncated - download full file)")
                st.code('\n'.join(preview_lines), language='xml')
            
            # Clean up temporary file
            if os.path.exists(temp_path):
                os.unlink(temp_path)
                
        except Exception as e:
            st.error(f"❌ Error during conversion: {str(e)}")
            with st.expander("Error Details", expanded=False):
                st.code(traceback.format_exc())
            
            # Clean up on error
            temp_path = locals().get('temp_path')
            if temp_path and os.path.exists(temp_path):
                os.unlink(temp_path)

if __name__ == "__main__":
    main()