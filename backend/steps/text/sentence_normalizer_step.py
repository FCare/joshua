"""
Step de normalisation de texte en phrases - inspiré du SentenceGrouper du SOCServer
Accumule les chunks de texte du LLM, les découpe en phrases complètes,
normalise les nombres et convertit les abréviations pour la TTS.
"""

import logging
import re
import asyncio
from messages.base_message import Message
from .number_converter import NumberToWordsConverter
from pipeline_framework import PipelineStep

# Configuration du logging
logger = logging.getLogger(__name__)

class SentenceNormalizerStep(PipelineStep):
    """
    Step qui normalise les chunks de texte en phrases complètes pour TTS.
    
    Fonctionnalités :
    - Accumule les chunks jusqu'à avoir des phrases complètes
    - Normalise les nombres français (supprime espaces séparateurs)
    - Convertit les chiffres romains en nombres arabes puis en mots
    - Expand les abréviations courantes
    - Nettoie le texte pour la synthèse vocale
    """
    
    def __init__(self, step_id: str, config: dict):
        super().__init__(step_id, config, handler=self._process_text_chunk)
        self.language_id = config.get('language_id', 'fr')
        
        # Buffer pour accumuler le texte
        self.sentence_buffer = ""
        
        # Convertisseur de nombres
        self.number_converter = NumberToWordsConverter(language=self.language_id)
        
        # Patterns des fins de phrase
        self.sentence_endings = r'[.!?]'
        
        # Abréviations qui ne terminent PAS une phrase
        self.abbreviations = {
            'fr': {
                'M.', 'Mme', 'Mlle', 'Dr', 'Prof', 'etc.', 'av.', 'ap.', 'J.-C.',
                'St', 'Ste', 'vs.', 'p.', 'pp.', 'vol.', 'n°', 'art.', 'cf.',
                'i.e.', 'e.g.', 'al.', 'ibid.', 'h', 'min', 's'
            },
            'en': {
                'Mr.', 'Mrs.', 'Ms.', 'Dr.', 'Prof.', 'etc.', 'vs.', 'p.', 'pp.', 
                'vol.', 'no.', 'art.', 'cf.', 'i.e.', 'e.g.', 'al.', 'ibid.', 
                'St.', 'Ave.', 'Blvd.', 'Inc.', 'Corp.'
            }
        }
        
        # Expansion des abréviations
        self.abbreviation_expansions = {
            'fr': {
                'etc.': 'et cætera', 'vs.': 'versus', 'cf.': 'confer',
                'i.e.': 'id est', 'e.g.': 'exempli gratia',
                'M.': 'Monsieur', 'Mme': 'Madame', 'Mlle': 'Mademoiselle',
                'Dr': 'Docteur', 'Prof': 'Professeur', 'St': 'Saint', 'Ste': 'Sainte',
                'h': 'heures', 'min': 'minutes', 's': 'secondes'
            },
            'en': {
                'Mr.': 'Mister', 'Mrs.': 'Missus', 'Ms.': 'Miss',
                'Dr.': 'Doctor', 'Prof.': 'Professor', 'etc.': 'et cetera',
                'vs.': 'versus', 'cf.': 'confer', 'i.e.': 'that is',
                'e.g.': 'for example', 'St.': 'Street', 'Ave.': 'Avenue'
            }
        }
        
        logger.info(f"SentenceNormalizerStep '{step_id}' configuré pour langue {self.language_id}")
    
    def init(self) -> bool:
        """Initialise le normalizer"""
        return True
    
    async def start(self):
        """Démarre le processing handler"""
        if not await super().start():
            return False
        
        logger.info(f"SentenceNormalizer '{self.name}' initialisé")
        return True
    
    async def _process_text_chunk(self, message: Message):
        """
        Handler ChunkQueue : traite les chunks de texte et produit des phrases normalisées
        """
        try:
            # 🎯 LOGIQUE SIMPLIFIÉE: Ignorer les signaux finish, ils iront directement au TTS
            if (hasattr(message, 'metadata') and message.metadata and
                message.metadata.get('chunk_type') == 'finish'):
                logger.info(f"🔄 SentenceNormalizer ignore le signal FINISH (il ira directement au TTS)")
                return
            
            # Vérifier à la fois 'data' et 'result' (OutputMessage vs InputMessage)
            text_chunk = None
            if hasattr(message, 'data') and message.data:
                text_chunk = message.data
            elif hasattr(message, 'result') and message.result:
                text_chunk = message.result
            
            if not text_chunk:
                return
            
            text_chunk = str(text_chunk)
            logger.debug(f"SentenceNormalizer reçu chunk: {repr(text_chunk)}")
            
            # Ajouter le chunk au buffer et récupérer les phrases complètes
            logger.debug(f"📝 SentenceNormalizer reçu chunk: {repr(text_chunk)}")
            complete_sentences = self._add_chunk(text_chunk)
            logger.info(f"🔍 SentenceNormalizer détecté {len(complete_sentences)} phrases complètes: {[repr(s) for s in complete_sentences]}")
            
            # Envoyer chaque phrase complète normalisée
            for sentence in complete_sentences:
                self._send_normalized_sentence(sentence, message, is_last_phrase=False)
            
        except Exception as e:
            logger.error(f"Erreur traitement chunk dans SentenceNormalizer: {e}")
    
    def _send_normalized_sentence(self, sentence: str, source_message: Message, is_last_phrase: bool = False):
        """
        Envoie une phrase normalisée avec les métadonnées appropriées
        """
        try:
            normalized = self._normalize_sentence(sentence)
            if not normalized.strip():
                return
            
            logger.info(f"📤 SentenceNormalizer envoie phrase{'(DERNIÈRE)' if is_last_phrase else ''}: {repr(normalized)}")
            
            # 🎯 CORRECTIF: Obtenir les données source depuis data OU result
            original_source_data = ""
            if hasattr(source_message, 'data') and source_message.data:
                original_source_data = source_message.data
            elif hasattr(source_message, 'result') and source_message.result:
                original_source_data = source_message.result
            
            # Créer message de sortie avec le texte normalisé
            # Préserver l'original_client_id pour le routage WebSocket
            new_metadata = {
                "source": self.name,
                "original_data": original_source_data,
                "is_last_phrase": is_last_phrase  # 🎯 MÉTADONNÉE CLÉ pour le TTS
            }
            
            if hasattr(source_message, 'metadata') and source_message.metadata:
                # Préserver l'original_client_id du message source
                if 'original_client_id' in source_message.metadata:
                    new_metadata['original_client_id'] = source_message.metadata['original_client_id']
            
            output_message = Message(
                type=source_message.type,
                data=normalized,
                metadata=new_metadata
            )
            
            self.output_queue.enqueue(output_message)
        
        except Exception as e:
            logger.error(f"Erreur envoi phrase normalisée: {e}")
    
    def _add_chunk(self, chunk: str) -> list:
        """
        Ajoute un chunk au buffer et retourne les phrases complètes détectées
        """
        if not chunk:
            return []
        
        # Ajouter le chunk au buffer
        self.sentence_buffer += chunk
        logger.debug(f"🔤 Buffer après ajout: {repr(self.sentence_buffer)}")
        
        # Chercher les fins de phrase
        complete_sentences = []
        sentence_endings = list(re.finditer(self.sentence_endings, self.sentence_buffer))
        logger.debug(f"🎯 Fins de phrase trouvées: {len(sentence_endings)} positions: {[m.span() for m in sentence_endings]}")
        
        if sentence_endings:
            last_sentence_end = -1
            
            for match in sentence_endings:
                end_pos = match.end()
                potential_sentence = self.sentence_buffer[:end_pos].strip()
                
                if self._is_true_sentence_end(potential_sentence, end_pos - 1):
                    if last_sentence_end == -1:
                        sentence = potential_sentence
                    else:
                        sentence = self.sentence_buffer[last_sentence_end + 1:end_pos].strip()
                    
                    if sentence:
                        logger.debug(f"✅ Phrase complète ajoutée: {repr(sentence)}")
                        complete_sentences.append(sentence)
                    last_sentence_end = end_pos - 1
            
            # Garder seulement ce qui vient après la dernière phrase complète
            remaining_buffer = self.sentence_buffer[last_sentence_end + 1:]
            logger.debug(f"🔄 Buffer restant: {repr(remaining_buffer)}")
            self.sentence_buffer = remaining_buffer
        
        logger.debug(f"📊 Retour de _add_chunk: {len(complete_sentences)} phrases: {[repr(s) for s in complete_sentences]}")
        return complete_sentences
    
    def _is_true_sentence_end(self, text: str, position: int) -> bool:
        """Vérifie si une position correspond vraiment à une fin de phrase"""
        words_before = text[:position + 1].split()
        
        if not words_before:
            return False
        
        last_word = words_before[-1]
        current_abbrevs = self.abbreviations.get(self.language_id, set())
        
        # Vérifier si le dernier mot est une abréviation connue
        if last_word in current_abbrevs:
            return False
        
        # Vérifier les abréviations composées
        if len(words_before) >= 2:
            last_two_words = " ".join(words_before[-2:])
            if last_two_words in current_abbrevs:
                return False
        
        return True
    
    def _normalize_sentence(self, sentence: str) -> str:
        """Normalise une phrase complète pour la TTS"""
        # 1. Normaliser les nombres français (supprimer espaces séparateurs)
        normalized = self._normalize_numbers(sentence)
        
        # 2. Convertir les chiffres romains en nombres arabes
        normalized = self._convert_roman_numerals(normalized)
        
        # 3. Séparer les nombres des unités (15h → 15 h)
        normalized = self._separate_numbers_from_units(normalized)
        
        # 4. Convertir les nombres en mots (basique)
        normalized = self._convert_numbers_to_words(normalized)
        
        # 5. Expansion des abréviations
        normalized = self._expand_abbreviations(normalized)
        
        # 6. Nettoyage final
        normalized = self._clean_text_for_tts(normalized)
        
        return normalized
    
    def _normalize_numbers(self, text: str) -> str:
        """Supprime les espaces séparateurs dans les nombres français"""
        # Pattern pour "2 300" → "2300"
        pattern = r'\b(\d{1,3}(?:\s\d{3})+)\b'
        
        def remove_spaces(match):
            return match.group(1).replace(' ', '')
        
        return re.sub(pattern, remove_spaces, text)
    
    def _convert_roman_numerals(self, text: str) -> str:
        """Convertit les chiffres romains en nombres arabes"""
        def roman_to_int(roman: str) -> int:
            values = {'I': 1, 'V': 5, 'X': 10, 'C': 100, 'M': 1000}
            
            for char in roman:
                if char not in values:
                    return None
            
            total = 0
            i = 0
            while i < len(roman):
                if i + 1 < len(roman) and values[roman[i]] < values[roman[i + 1]]:
                    total += values[roman[i + 1]] - values[roman[i]]
                    i += 2
                else:
                    total += values[roman[i]]
                    i += 1
            return total
        
        def replace_roman(match):
            prefix = match.group(1)
            roman = match.group(2)
            suffix = match.group(3)
            
            number = roman_to_int(roman)
            if number is None or len(roman) > 7:
                return match.group(0)
            
            return prefix + str(number) + suffix
        
        # Ordinaux français : "Ier" → "1er"
        ordinal_pattern = r'(\s|^|[^\w])([MXVCI]+)(er|e|ème)(\s|$|[^\w])'
        text = re.sub(ordinal_pattern, replace_roman, text)
        
        # 🎯 Chiffres romains purs : "XIV" → "14"
        # MAIS ignorer les contractions françaises comme "C'est"
        def replace_roman_safe(match):
            prefix = match.group(1)
            roman = match.group(2)
            suffix = match.group(3)
            
            # Si c'est une contraction avec apostrophe, ne pas convertir
            if suffix.startswith("'"):
                return match.group(0)  # Garder tel quel
            
            number = roman_to_int(roman)
            if number is None or len(roman) > 7:
                return match.group(0)
            
            return prefix + str(number) + suffix
        
        pure_pattern = r'(\s|^|[^\w])([MXVCI]+)(\s|$|[^\w])'
        text = re.sub(pure_pattern, replace_roman_safe, text)
        
        return text
    
    def _separate_numbers_from_units(self, text: str) -> str:
        """Sépare les nombres des unités : 15h → 15 h"""
        pattern = r'\b(\d+)(h|min|s|km|m|cm|mm|kg|g|l|ml)\b'
        return re.sub(pattern, r'\1 \2', text)
    
    def _convert_numbers_to_words(self, text: str) -> str:
        """Convertit les nombres arabes et ordinaux en mots français"""
        # D'abord traiter les ordinaux (ex: "1er" → "premier", "2e" → "deuxième")
        text_with_ordinals = self.number_converter._convert_ordinals(text)
        
        def replace_number(match):
            """Remplace un nombre par son équivalent en lettres"""
            number_str = match.group(0)
            try:
                number = int(number_str)
                # Limiter à des nombres raisonnables pour éviter les performances dégradées
                if 0 <= number <= 999999:
                    words = self.number_converter.number_to_words(number)
                    return words
                else:
                    return number_str  # Garder tel quel si trop grand
            except (ValueError, OverflowError):
                return number_str  # Garder tel quel si conversion impossible
        
        # Pattern pour détecter les nombres entiers (pas les décimaux ni les dates)
        # Évite les numéros de téléphone, dates, etc.
        number_pattern = r'\b\d{1,6}\b'
        
        return re.sub(number_pattern, replace_number, text_with_ordinals)
    
    def _expand_abbreviations(self, text: str) -> str:
        """Expanse les abréviations vers leurs formes complètes"""
        expansions = self.abbreviation_expansions.get(self.language_id, {})
        
        # Trier par longueur décroissante
        sorted_abbrevs = sorted(expansions.items(), key=lambda x: len(x[0]), reverse=True)
        
        result = text
        for abbrev, expansion in sorted_abbrevs:
            pattern = r'\b' + re.escape(abbrev) + r'\b'
            result = re.sub(pattern, expansion, result, flags=re.IGNORECASE)
        
        return result
    
    def _clean_text_for_tts(self, text: str) -> str:
        """Nettoie le texte pour la synthèse vocale"""
        # Supprimer formatage markdown
        text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)  # **gras** → gras
        text = re.sub(r'\*(.+?)\*', r'\1', text)      # *italique* → italique
        text = re.sub(r'`(.+?)`', r'\1', text)        # `code` → code
        
        # Supprimer caractères de formatage indésirables
        text = re.sub(r'[_~`]', ' ', text)
        
        # Normaliser les espaces multiples
        text = re.sub(r'\s+', ' ', text)
        
        return text.strip()
    
    def cleanup(self):
        """Nettoyage des ressources"""
        # Traiter le buffer restant si nécessaire (version sync pour compatibilité)
        if self.sentence_buffer.strip():
            logger.info(f"Buffer restant à la fin: {repr(self.sentence_buffer)}")
        
        logger.info(f"SentenceNormalizer '{self.name}' nettoyé")