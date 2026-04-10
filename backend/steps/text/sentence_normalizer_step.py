"""
Step de normalisation de texte - Version simplifiée
Accumule les chunks de texte du LLM et normalise tout à la fin.
"""

import logging
import re
import asyncio
from messages.base_message import BaseMessage
from .number_converter import NumberToWordsConverter
from pipeline_framework import PipelineStep

# Configuration du logging
logger = logging.getLogger(__name__)

class SentenceNormalizerStep(PipelineStep):
    """
    Step qui accumule le texte et le normalise à la fin.
    
    Fonctionnalités :
    - Accumule tous les chunks de ChatResponseMessage
    - Sur ChatFinishMessage : normalise et envoie le texte complet
    - Normalise les nombres français
    - Convertit les chiffres romains en nombres arabes puis en mots
    - Expand les abréviations courantes
    """
    
    def __init__(self, step_id: str, config: dict):
        super().__init__(step_id, config, handler=self._process_text_chunk)
        self.language_id = config.get('language_id', 'fr')
        
        # Buffer pour accumuler tout le texte
        self.text_buffer = ""
        
        # Convertisseur de nombres
        self.number_converter = NumberToWordsConverter(language=self.language_id)
        
        # Expansion des abréviations
        self.abbreviation_expansions = {
            'fr': {
                'etc.': 'et cætera', 'vs.': 'versus', 'cf.': 'confer',
                'i.e.': 'id est', 'e.g.': 'exempli gratia',
                # Titres de civilité
                'M.': 'Monsieur', 'Mr': 'Monsieur', 'Mr.': 'Monsieur',
                'Mme': 'Madame', 'Mme.': 'Madame', 'Mrs': 'Madame', 'Mrs.': 'Madame',
                'Mlle': 'Mademoiselle', 'Mlle.': 'Mademoiselle', 'Ms': 'Mademoiselle', 'Ms.': 'Mademoiselle',
                # Professions et titres
                'Dr': 'Docteur', 'Dr.': 'Docteur', 'Prof': 'Professeur', 'Prof.': 'Professeur',
                'St': 'Saint', 'St.': 'Saint', 'Ste': 'Sainte', 'Ste.': 'Sainte',
                'Pr': 'Professeur', 'Pr.': 'Professeur',
                # Unités temporelles
                'h': 'heures', 'min': 'minutes', 'min.': 'minutes', 'sec': 'secondes', 'sec.': 'secondes',
                # Autres abréviations courantes
                'av.': 'avenue', 'bd': 'boulevard', 'bd.': 'boulevard',
                'ch.': 'chapitre', 'p.': 'page', 'pp.': 'pages',
                'vol.': 'volume', 'n°': 'numéro', 'art.': 'article'
            },
            'en': {
                'Mr.': 'Mister', 'Mr': 'Mister', 'Mrs.': 'Missus', 'Mrs': 'Missus',
                'Ms.': 'Miss', 'Ms': 'Miss', 'Miss.': 'Miss',
                'Dr.': 'Doctor', 'Dr': 'Doctor', 'Prof.': 'Professor', 'Prof': 'Professor',
                'etc.': 'et cetera', 'etc': 'et cetera',
                'vs.': 'versus', 'vs': 'versus', 'cf.': 'confer', 'cf': 'confer',
                'i.e.': 'that is', 'e.g.': 'for example',
                'St.': 'Street', 'St': 'Street', 'Ave.': 'Avenue', 'Ave': 'Avenue',
                'Blvd.': 'Boulevard', 'Blvd': 'Boulevard',
                'Inc.': 'Incorporated', 'Inc': 'Incorporated',
                'Corp.': 'Corporation', 'Corp': 'Corporation'
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
    
    async def _process_text_chunk(self, message: BaseMessage):
        """
        Handler : accumule les chunks ou normalise et envoie à la fin
        """
        from messages.chat_message import ChatResponseMessage, ChatFinishMessage
        
        allowed_classes = (ChatResponseMessage, ChatFinishMessage)
        if not isinstance(message, allowed_classes):
            return
            
        if isinstance(message, ChatResponseMessage):
            # Simplement accumuler le texte
            if message.text:
                self.text_buffer += str(message.text)
                logger.info(f"📝 Texte accumulé: {repr(message.text)} - Buffer total: {repr(self.text_buffer)}")
                
        elif isinstance(message, ChatFinishMessage):
            # Normaliser tout le texte accumulé et l'envoyer
            logger.info(f"🏁 Fin du chat - normalisation du texte complet: {repr(self.text_buffer)}")
            if self.text_buffer.strip():
                normalized_text = self._normalize_text(self.text_buffer)
                self._send_normalized_text(normalized_text)
            
            # Envoyer le message de fin
            self._send_end_message()
            
            # Reset du buffer pour le prochain message
            self.text_buffer = ""

    def _send_normalized_text(self, text: str):
        """
        Envoie le texte normalisé complet
        """
        try:
            if not text.strip():
                logger.warning(f"⚠️ Texte normalisé vide, abandonnée")
                return
            
            from messages.text_message import SentenceMessage
            output_message = SentenceMessage(
                text=text,
                is_last=False
            )
            
            self.output_queue.enqueue(output_message)
            logger.info(f"📤 Texte normalisé envoyé: {repr(text)}")
        
        except Exception as e:
            logger.error(f"Erreur envoi texte normalisé: {e}")

    def _send_end_message(self):
        """
        Envoie le message de fin
        """
        try:
            from messages.text_message import SentenceMessage
            output_message = SentenceMessage(
                text="",
                is_last=True
            )
            
            self.output_queue.enqueue(output_message)
            logger.info(f"🏁 Message de fin envoyé")
        
        except Exception as e:
            logger.error(f"Erreur envoi message de fin: {e}")
    
    def _normalize_text(self, text: str) -> str:
        """Normalise le texte complet pour la TTS"""
        logger.info(f"🔧 DÉBUT normalisation: {repr(text)}")
        
        # 1. Normaliser les nombres français (supprimer espaces séparateurs)
        normalized = self._normalize_numbers(text)
        logger.info(f"🔧 Étape 1 (espaces nombres): {repr(normalized)}")
        
        # 2. Convertir les chiffres romains en nombres arabes
        normalized = self._convert_roman_numerals(normalized)
        logger.info(f"🔧 Étape 2 (chiffres romains): {repr(normalized)}")
        
        # 3. Séparer les nombres des unités (15h → 15 h)
        normalized = self._separate_numbers_from_units(normalized)
        logger.info(f"🔧 Étape 3 (séparation unités): {repr(normalized)}")
        
        # 4. Convertir les nombres en mots
        normalized = self._convert_numbers_to_words(normalized)
        logger.info(f"🔧 Étape 4 (nombres en mots): {repr(normalized)}")
        
        # 5. Expansion des abréviations
        normalized = self._expand_abbreviations(normalized)
        logger.info(f"🔧 Étape 5 (abréviations): {repr(normalized)}")
        
        # 6. Nettoyage final
        normalized = self._clean_text_for_tts(normalized)
        logger.info(f"🔧 FIN normalisation: {repr(normalized)}")
        
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
        
        # Ordinaux français : "Ier" → "1er" (en gardant les espaces)
        def replace_roman_ordinal(match):
            prefix = match.group(1)
            roman = match.group(2)
            suffix = match.group(3)
            remaining = match.group(4)
            
            number = roman_to_int(roman)
            if number is None or len(roman) > 7:
                return match.group(0)
            
            return prefix + str(number) + suffix + remaining
        
        ordinal_pattern = r'(\s|^|[^\w])([MXVCI]+)(er|e|ème)(\s|$|[^\w])'
        text = re.sub(ordinal_pattern, replace_roman_ordinal, text)
        
        # Chiffres romains purs : "XIV" → "14" (majuscules et minuscules)
        def replace_roman_safe(match):
            prefix = match.group(1)
            roman = match.group(2).upper()  # Convertir en majuscules pour le traitement
            suffix = match.group(3)
            
            # Si c'est une contraction avec apostrophe, ne pas convertir
            if suffix.startswith("'"):
                return match.group(0)
            
            number = roman_to_int(roman)
            if number is None or len(roman) > 7:
                return match.group(0)
            
            return prefix + str(number) + suffix
        
        # Pattern pour majuscules ET minuscules
        pure_pattern = r'(\s|^|[^\w])([MXVCImxvci]+)(\s|$|[^\w])'
        text = re.sub(pure_pattern, replace_roman_safe, text, flags=re.IGNORECASE)
        
        return text
    
    def _separate_numbers_from_units(self, text: str) -> str:
        """Sépare les nombres des unités : 15h → 15 h, 14h30 → 14 h 30"""
        # Traiter d'abord les heures avec minutes (14h30 → 14 h 30)
        text = re.sub(r'\b(\d+)h(\d+)\b', r'\1 h \2', text)
        
        # Puis les unités simples
        pattern = r'\b(\d+)(h|min|s|km|m|cm|mm|kg|g|l|ml)\b'
        return re.sub(pattern, r'\1 \2', text)
    
    def _convert_numbers_to_words(self, text: str) -> str:
        """Convertit les nombres arabes et ordinaux en mots français"""
        # D'abord traiter les ordinaux
        text_with_ordinals = self.number_converter._convert_ordinals(text)
        
        def replace_number(match):
            """Remplace un nombre par son équivalent en lettres"""
            number_str = match.group(0)
            try:
                # Vérifier si c'est un décimal
                if '.' in number_str or ',' in number_str:
                    return self.number_converter.decimal_to_words(number_str)
                else:
                    number = int(number_str)
                    # Limiter à des nombres raisonnables
                    if 0 <= number <= 999999:
                        words = self.number_converter.number_to_words(number)
                        return words
                    else:
                        return number_str
            except (ValueError, OverflowError):
                return number_str
        
        # Pattern pour détecter les nombres entiers ET décimaux
        number_pattern = r'\b\d{1,6}(?:[.,]\d{1,3})?\b'
        
        return re.sub(number_pattern, replace_number, text_with_ordinals)
    
    def _expand_abbreviations(self, text: str) -> str:
        """Expanse les abréviations vers leurs formes complètes"""
        expansions = self.abbreviation_expansions.get(self.language_id, {})
        
        # Trier par longueur décroissante
        sorted_abbrevs = sorted(expansions.items(), key=lambda x: len(x[0]), reverse=True)
        
        result = text
        for abbrev, expansion in sorted_abbrevs:
            # Si l'abréviation se termine par un point, l'inclure dans le remplacement
            if abbrev.endswith('.'):
                # Pattern pour capturer l'abréviation avec son point
                pattern = r'\b' + re.escape(abbrev[:-1]) + r'\.'
                result = re.sub(pattern, expansion, result, flags=re.IGNORECASE)
            else:
                # Pattern standard pour les abréviations sans point
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
        if self.text_buffer.strip():
            logger.info(f"Buffer restant à la fin: {repr(self.text_buffer)}")
        
        logger.info(f"SentenceNormalizer '{self.name}' nettoyé")