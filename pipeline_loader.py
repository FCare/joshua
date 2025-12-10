import json
import os
from typing import Dict, List, Optional, Any
from pathlib import Path

from pipeline_framework import Pipeline, PipelineStep
from messages.base_message import InputMessage, OutputMessage
from steps.websocket.websocket_step import WebSocketStep
from steps.asr.kyutai_asr_step import KyutaiASRStep
from steps.tts.chatterbox_tts_step import ChatterboxTTSStep
from steps.chat.openai_chat_step import OpenAIChatStep
from steps.utils.duplicator_step import DuplicatorStep
from steps.text.sentence_normalizer_step import SentenceNormalizerStep


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
            "chatterbox_tts": ChatterboxTTSStep,
            "openai_chat": OpenAIChatStep,
            "duplicator": DuplicatorStep,
            "sentence_normalizer": SentenceNormalizerStep
        }
        
        step_class = type_class_mapping.get(step_type)
        if not step_class:
            return None
        
        try:
            return step_class(step_id, config)
        except Exception as e:
            print(f"❌ Erreur création step {step_type}/{step_id}: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def _create_step_config_from_definition(self, step_instance: Dict, step_definition_ref: str) -> Optional[Dict]:
        """Combine step_definition avec step_instance pour créer un step_config"""
        step_definition = self.step_definitions.get(step_definition_ref)
        if not step_definition:
            return None
        
        # Extraire les infos nécessaires de la step_definition
        # Utiliser un mapping pour convertir les noms en types compatibles
        step_name = step_definition.get("name") or step_definition_ref
        
        # Mapping des noms de step_definition vers types pipeline
        name_to_type_mapping = {
            "websocket_server": "websocket_server",
            "OpenAI Chat Step": "openai_chat",
            "Duplicator Step": "duplicator",
            "Sentence Normalizer Step": "sentence_normalizer",
            "chatterbox_tts": "chatterbox_tts",
            "Kyutai ASR Step": "kyutai_asr"
        }
        
        step_type = name_to_type_mapping.get(step_name, step_name)
        step_class_name = step_definition.get("class_name")
        step_module_path = step_definition.get("module_path")
        
        # Config par défaut de la step_definition
        default_config = {}
        if "default_config" in step_definition:
            default_config = step_definition["default_config"].copy()
        elif "example_config" in step_definition:
            default_config = step_definition["example_config"].copy()
        elif "configuration" in step_definition:
            # Extraire les valeurs par défaut du schéma de configuration
            config_schema = step_definition["configuration"]
            for param_name, param_def in config_schema.items():
                if "default" in param_def:
                    default_config[param_name] = param_def["default"]
        
        # Combiner default_config avec config_overrides de l'instance
        instance_overrides = step_instance.get("config_overrides", {})
        # Fallback vers "config" pour compatibilité avec ancienne structure
        if not instance_overrides:
            instance_overrides = step_instance.get("config", {})
        
        merged_config = {**default_config, **instance_overrides}
        
        # Créer le step_config dans l'ancien format pour compatibilité
        step_config = {
            "id": step_instance.get("instance_id"),
            "type": step_type,
            "position": step_instance.get("position", 0),
            "config": merged_config
        }
        
        return step_config
    
    def create_pipeline_from_definition(self, pipeline_id: str,
                                       custom_config: Optional[Dict] = None) -> Optional[Pipeline]:
        pipeline_def = self.get_pipeline_definition(pipeline_id)
        if not pipeline_def:
            return None
        
        pipeline_name = pipeline_def.get("name", pipeline_id)
        pipeline = Pipeline(pipeline_name)
        
        # Support pour la nouvelle structure step_instances (avec références step_definitions)
        step_instances = pipeline_def.get("step_instances", [])
        if not step_instances:
            # Fallback vers l'ancienne structure "steps"
            step_instances = pipeline_def.get("steps", [])
        
        steps_map = {}
        
        for step_instance in step_instances:
            step_instance_id = step_instance.get("instance_id") or step_instance.get("id") or step_instance.get("step_id")
            
            # Résoudre la step_definition si présente
            step_definition_ref = step_instance.get("step_definition")
            if step_definition_ref:
                # Créer la config en combinant step_definition + instance config
                step_config = self._create_step_config_from_definition(step_instance, step_definition_ref)
                if not step_config:
                    print(f"❌ Step definition '{step_definition_ref}' introuvable pour instance '{step_instance_id}'")
                    print(f"🔍 Step definitions disponibles: {list(self.step_definitions.keys())}")
                    continue
            else:
                # Ancienne structure directe
                step_config = step_instance
            
            if custom_config and step_instance_id in custom_config:
                step_config["config"].update(custom_config[step_instance_id])
            
            step = self.create_step_from_config(step_config)
            if step:
                print(f"✅ Step '{step_instance_id}' créé avec succès")
                pipeline.add_step(step)
                steps_map[step_instance_id] = step
            else:
                print(f"❌ Impossible de créer le step '{step_instance_id}' avec config: {step_config}")
                return None
        
        connections = pipeline_def.get("connections", [])
        for connection in connections:
            from_step = connection.get("from") or connection.get("from_step")
            to_targets = connection.get("to") or connection.get("to_step")
            
            if isinstance(to_targets, str):
                # Connexion simple
                try:
                    pipeline.connect_steps(from_step, to_targets)
                except Exception as e:
                    return None
            elif isinstance(to_targets, list):
                # Connexions multiples - l'ordre détermine l'index de branche
                from_step_obj = steps_map.get(from_step)
                if from_step_obj and hasattr(from_step_obj, 'add_output_queue'):
                    print(f"🔗 Multi-connection: {from_step} → {to_targets}")
                    for target_id in to_targets:
                        target_step = steps_map.get(target_id)
                        if target_step and hasattr(target_step, 'input_queue'):
                            from_step_obj.add_output_queue(target_step.input_queue)
                            print(f"✅ Connection: {from_step} → {target_id}")
        
        # Plus besoin de multi_connections - syntaxe unifiée
        
        self._setup_bidirectional_connections(pipeline, pipeline_def, steps_map)
        
        return pipeline
    
    # Méthode obsolète - remplacée par syntaxe unifiée dans connections

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
            
            elif "chat" in pipeline_name.lower():
                chat_step = steps_map.get("openai_chat")
                ws_step = steps_map.get("websocket_server")
                
                if chat_step and ws_step:
                    chat_step.set_output_queue(ws_step.input_queue)
                    ws_step.set_output_queue(chat_step.input_queue)
                    
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