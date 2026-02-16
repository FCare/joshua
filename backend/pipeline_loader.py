import json
import os
import importlib
from typing import Dict, List, Optional, Any
from pathlib import Path

from pipeline_framework import Pipeline, PipelineStep
from messages.base_message import InputMessage, OutputMessage


class PipelineLoader:
    
    def __init__(self, step_definitions_dir: str = "step_definitions",
                 pipeline_definitions_dir: str = "pipeline_definitions"):
        self.step_definitions_dir = Path(step_definitions_dir)
        self.pipeline_definitions_dir = Path(pipeline_definitions_dir)
        self.step_definitions = {}
        self.pipeline_definitions = {}
        self.step_class_cache = {}  # Cache pour éviter d'importer plusieurs fois
        
        self.load_step_definitions()
        self.load_pipeline_definitions()
    
    def _import_step_class(self, step_definition: Dict) -> Optional[type]:
        """Importe dynamiquement une classe de step basée sur module_path et class_name"""
        module_path = step_definition.get("module_path")
        class_name = step_definition.get("class_name")
        
        if not module_path or not class_name:
            return None
        
        # Utiliser le cache si déjà importé
        cache_key = f"{module_path}.{class_name}"
        if cache_key in self.step_class_cache:
            return self.step_class_cache[cache_key]
        
        try:
            module = importlib.import_module(module_path)
            step_class = getattr(module, class_name)
            self.step_class_cache[cache_key] = step_class
            return step_class
        except (ImportError, AttributeError) as e:
            print(f"❌ Impossible d'importer {class_name} depuis {module_path}: {e}")
            return None
    
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
        
        # Trouver la step definition correspondante
        step_definition = self.step_definitions.get(step_type)
        if not step_definition:
            print(f"❌ Step definition '{step_type}' introuvable")
            return None
        
        # Importer dynamiquement la classe
        step_class = self._import_step_class(step_definition)
        if not step_class:
            print(f"❌ Impossible d'importer la classe pour '{step_type}'")
            return None
        
        try:
            return step_class(step_id, config)
        except Exception as e:
            print(f"❌ Erreur création step {step_type}/{step_id}: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def _create_step_config_from_definition(self, step_instance: Dict, step_definition_ref: str, pipeline_def: Optional[Dict] = None) -> Optional[Dict]:
        """Combine step_definition avec step_instance pour créer un step_config"""
        step_definition = self.step_definitions.get(step_definition_ref)
        if not step_definition:
            return None
        
        # Utiliser directement le nom de la step definition comme type
        step_name = step_definition.get("name") or step_definition_ref
        step_type = step_name  # Plus besoin de mapping, on utilise le nom directement
        step_class_name = step_definition.get("class_name")
        step_module_path = step_definition.get("module_path")
        
        # Config par défaut de la step_definition
        config = {}

        if "config_schema" in step_definition:
            # Extraire les valeurs par défaut du format JSON Schema
            config_schema = step_definition["config_schema"]
            properties = config_schema.get("properties", {})
            for param_name, param_def in properties.items():
                if "default" in param_def:
                    config[param_name] = param_def["default"]
        
        # Combiner config avec config_overrides de l'instance
        instance_overrides = step_instance.get("config_overrides", {})
        # Fallback vers "config" pour compatibilité avec ancienne structure
        if not instance_overrides:
            instance_overrides = step_instance.get("config", {})
        
        merged_config = {**config, **instance_overrides}
        
        # Si c'est un WebSocketStep, ajouter les capacités du pipeline
        if step_definition_ref == "websocket_server" and pipeline_def:
            pipeline_capabilities = pipeline_def.get("capabilities", {})
            pipeline_name = pipeline_def.get("name", "Unknown Pipeline")
            merged_config["pipeline_capabilities"] = pipeline_capabilities
            merged_config["pipeline_name"] = pipeline_name
        
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
                step_config = self._create_step_config_from_definition(step_instance, step_definition_ref, pipeline_def)
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
        
        return pipeline
    
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