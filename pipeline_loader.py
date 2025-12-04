import json
import os
from typing import Dict, List, Optional, Any
from pathlib import Path

from pipeline_framework import Pipeline, PipelineStep
from messages.base_message import InputMessage, OutputMessage
from steps.websocket.websocket_step import WebSocketStep
from steps.asr.kyutai_asr_step import KyutaiASRStep
from steps.tts.chatterbox_tts_step import ChatterboxTTSStep


class PipelineLoader:
    
    def __init__(self, step_definitions_dir: str = "step_definitions", 
                 pipeline_definitions_dir: str = "pipeline_definitions"):
        self.step_definitions_dir = Path(step_definitions_dir)
        self.pipeline_definitions_dir = Path(pipeline_definitions_dir)
        self.step_definitions = {}
        self.pipeline_definitions = {}
        
        self.load_step_definitions()
        self.load_pipeline_definitions()
    
    def load_step_definitions(self):
        if not self.step_definitions_dir.exists():
            return
            
        for json_file in self.step_definitions_dir.glob("*.json"):
            try:
                with open(json_file, 'r', encoding='utf-8') as f:
                    step_def = json.load(f)
                    step_type = step_def.get("name") or step_def.get("step_type")
                    if step_type:
                        self.step_definitions[step_type] = step_def
            except Exception as e:
                pass
    
    def load_pipeline_definitions(self):
        if not self.pipeline_definitions_dir.exists():
            return
            
        for json_file in self.pipeline_definitions_dir.glob("*.json"):
            try:
                with open(json_file, 'r', encoding='utf-8') as f:
                    pipeline_def = json.load(f)
                    pipeline_id = pipeline_def.get("name") or pipeline_def.get("pipeline_id")
                    if pipeline_id:
                        self.pipeline_definitions[pipeline_id] = pipeline_def
            except Exception as e:
                pass
    
    def get_available_step_types(self) -> List[str]:
        return list(self.step_definitions.keys())
    
    def get_available_pipelines(self) -> List[str]:
        return list(self.pipeline_definitions.keys())
    
    def get_step_definition(self, step_type: str) -> Optional[Dict]:
        return self.step_definitions.get(step_type)
    
    def get_pipeline_definition(self, pipeline_id: str) -> Optional[Dict]:
        return self.pipeline_definitions.get(pipeline_id)
    
    def validate_step_config(self, step_type: str, config: Dict) -> bool:
        step_def = self.get_step_definition(step_type)
        if not step_def:
            return False
        
        required_config = step_def.get("configuration", {})
        for param_name, param_def in required_config.items():
            if param_name in config:
                param_type = param_def.get("type")
                param_value = config[param_name]
                
                if not self._validate_parameter_type(param_value, param_type):
                    return False
                
                allowed_values = param_def.get("values")
                if allowed_values and param_value not in allowed_values:
                    return False
        
        return True
    
    def _validate_parameter_type(self, value: Any, expected_type: str) -> bool:
        type_mapping = {
            "string": str,
            "integer": int,
            "float": float,
            "boolean": bool,
            "array": list,
            "object": dict
        }
        
        expected_python_type = type_mapping.get(expected_type)
        if not expected_python_type:
            return True
        
        return isinstance(value, expected_python_type)
    
    def create_step_from_config(self, step_config: Dict) -> Optional[PipelineStep]:
        step_id = step_config.get("id") or step_config.get("step_id")
        step_type = step_config.get("type") or step_config.get("step_type")
        config = step_config.get("config", {})
        
        type_class_mapping = {
            "websocket_server": WebSocketStep,
            "audio_websocket_server": WebSocketStep,
            "kyutai_asr": KyutaiASRStep,
            "chatterbox_tts": ChatterboxTTSStep
        }
        
        step_class = type_class_mapping.get(step_type)
        if not step_class:
            return None
        
        try:
            return step_class(step_id, config)
        except Exception as e:
            return None
    
    def create_pipeline_from_definition(self, pipeline_id: str, 
                                       custom_config: Optional[Dict] = None) -> Optional[Pipeline]:
        pipeline_def = self.get_pipeline_definition(pipeline_id)
        if not pipeline_def:
            return None
        
        pipeline_name = pipeline_def.get("name", pipeline_id)
        pipeline = Pipeline(pipeline_name)
        
        steps_config = pipeline_def.get("steps", [])
        steps_map = {}
        
        for step_config in sorted(steps_config, key=lambda x: x.get("position", 0)):
            step_id = step_config.get("id") or step_config.get("step_id")
            if custom_config and step_id in custom_config:
                step_config["config"].update(custom_config[step_id])
            
            step = self.create_step_from_config(step_config)
            if step:
                pipeline.add_step(step)
                steps_map[step_id] = step
            else:
                return None
        
        connections = pipeline_def.get("connections", [])
        for connection in connections:
            from_step = connection.get("from") or connection.get("from_step")
            to_step = connection.get("to") or connection.get("to_step")
            
            try:
                pipeline.connect_steps(from_step, to_step)
            except Exception as e:
                return None
        
        self._setup_bidirectional_connections(pipeline, pipeline_def, steps_map)
        
        return pipeline
    
    def _setup_bidirectional_connections(self, pipeline: Pipeline, pipeline_def: Dict, steps_map: Dict):
        try:
            pipeline_name = pipeline_def.get("name") or pipeline_def.get("pipeline_id")
            if "audio_transcription" in pipeline_name:
                asr_step = steps_map.get("asr_step")
                ws_step = steps_map.get("websocket_server")
                
                if asr_step and ws_step:
                    asr_step.set_output_queue(ws_step.input_queue)
            
            elif "text_to_speech" in pipeline_name:
                tts_step = steps_map.get("chatterbox_tts")
                ws_step = steps_map.get("websocket_server")
                
                if tts_step and ws_step:
                    tts_step.set_output_queue(ws_step.input_queue)
                    
        except Exception as e:
            pass
    
    def list_pipelines_info(self) -> List[Dict]:
        pipelines_info = []
        for pipeline_id, pipeline_def in self.pipeline_definitions.items():
            info = {
                "id": pipeline_id,
                "name": pipeline_def.get("name"),
                "description": pipeline_def.get("description")
            }
            pipelines_info.append(info)
        return pipelines_info