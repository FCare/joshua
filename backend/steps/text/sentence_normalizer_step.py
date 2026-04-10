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
                # Unités de mesure
                '°': 'degrés', '°C': 'degrés', '°F': 'degrés',
                'mm': 'millimètres', 'cm': 'centimètres', 'km': 'kilomètres',
                'mg': 'milligrammes', 'kg': 'kilogrammes',
                'ml': 'millilitres',
                # Abréviations culinaires
                'càc': 'cuillère à café', 'c.à.c.': 'cuillère à café',
                'cas': 'cuillère à soupe', 'c.à.s.': 'cuillère à soupe',
                # Note: 'm', 'g', 'l' supprimés car trop courts et causent des faux positifs
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
        Handler : accumule les chunks et traite les phrases complètes en temps réel
        """
        from messages.chat_message import ChatResponseMessage, ChatFinishMessage
        
        allowed_classes = (ChatResponseMessage, ChatFinishMessage)
        if not isinstance(message, allowed_classes):
            return
            
        if isinstance(message, ChatResponseMessage):
            # Accumuler le texte
            if message.text:
                self.text_buffer += str(message.text)
                logger.info(f"📝 Texte accumulé: {repr(message.text)} - Buffer total: {repr(self.text_buffer)}")
                
                # Détecter et traiter les phrases complètes
                self._detect_and_process_complete_sentences()
                
        elif isinstance(message, ChatFinishMessage):
            # Normaliser tout le texte restant dans le buffer et l'envoyer
            logger.info(f"🏁 Fin du chat - normalisation du texte restant: {repr(self.text_buffer)}")
            if self.text_buffer.strip():
                normalized_text = self._normalize_text(self.text_buffer)
                self._send_normalized_text(normalized_text)
            
            # Envoyer le message de fin
            self._send_end_message()
            
            # Reset du buffer pour le prochain message
            self.text_buffer = ""

    def _detect_and_process_complete_sentences(self):
        """
        Détecte les phrases complètes dans le buffer et les traite immédiatement
        Pattern : lettre + {.,!,?} + espace optionnel + majuscule
        """
        if not self.text_buffer:
            return
        
        # Pattern pour détecter une phrase complète :
        # lettre/chiffre + ponctuation de fin + espace optionnel + majuscule
        sentence_pattern = r'([a-zA-Zàâäéèêëïîôöùûüÿç0-9][^.!?]*[.!?])\s*([A-ZÀÂÄÉÈÊËÏÎÔÖÙÛÜŸÇ])'
        
        match = re.search(sentence_pattern, self.text_buffer)
        if match:
            sentence_end = match.start(2)  # Position du début du prochain mot (majuscule)
            complete_sentence = self.text_buffer[:sentence_end].strip()
            remaining_text = self.text_buffer[sentence_end:]
            
            logger.info(f"🔍 Phrase complète détectée: {repr(complete_sentence)}")
            
            # Normaliser et envoyer la phrase complète
            if complete_sentence:
                normalized_sentence = self._normalize_text(complete_sentence)
                self._send_normalized_text(normalized_sentence)
            
            # Garder le reste dans le buffer
            self.text_buffer = remaining_text
            logger.info(f"📝 Buffer restant: {repr(self.text_buffer)}")
            
            # Récursion pour détecter d'autres phrases complètes
            self._detect_and_process_complete_sentences()

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
        
        # 4. Expansion des abréviations (AVANT conversion nombres en mots pour garder contexte numérique)
        normalized = self._expand_abbreviations(normalized)
        logger.info(f"🔧 Étape 4 (abréviations): {repr(normalized)}")
        
        # 5. Convertir les nombres en mots
        normalized = self._convert_numbers_to_words(normalized)
        logger.info(f"🔧 Étape 5 (nombres en mots): {repr(normalized)}")
        
        # 6. Corriger les espaces autour des unités
        normalized = self._fix_unit_spacing(normalized)
        logger.info(f"🔧 Étape 6 (espaces unités): {repr(normalized)}")
        
        # 7. Nettoyage final
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
        
        # Pattern pour chiffres romains UNIQUEMENT en majuscules
        # Exemples valides: I, II, III, IV, V, VI, VII, VIII, IX, X, XI, XII, XIII, XIV, XV, XVI, etc.
        # Évite les unités de mesure comme °C, mm, cm, etc.
        def replace_roman_safe_filtered(match):
            prefix = match.group(1)
            roman = match.group(2)
            suffix = match.group(3)
            
            # Si c'est une contraction avec apostrophe, ne pas convertir
            if suffix.startswith("'"):
                return match.group(0)
            
            # Si c'est précédé de °, ne pas convertir (pour °C)
            if prefix.endswith("°"):
                return match.group(0)
                
            # Si c'est suivi d'une lettre minuscule, c'est probablement une unité (mm, cm, etc.)
            if suffix and suffix[0].islower():
                return match.group(0)
            
            number = roman_to_int(roman)
            if number is None or len(roman) > 7:
                return match.group(0)
            
            return prefix + str(number) + suffix
        
        pure_pattern = r'(\s|^|[^\w°])([IVXLCDM]{2,7})(\s|$|[^\w])'  # Minimum 2 caractères pour éviter lettres isolées
        text = re.sub(pure_pattern, replace_roman_safe_filtered, text)
        
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
                        # Vérifier si le nombre se termine par 1 (1, 21, 31, 41, 101, etc.)
                        feminine = False
                        if number % 10 == 1 and number % 100 != 11:  # Exclure 11, 111, etc.
                            # Chercher le mot qui suit le nombre dans le texte
                            start_pos = match.start()
                            end_pos = match.end()
                            remaining_text = text_with_ordinals[end_pos:]
                            
                            # Extraire le premier mot qui suit
                            next_word_match = re.match(r'\s*([a-zA-Zàâäéèêëïîôöùûüÿç]+)', remaining_text)
                            if next_word_match:
                                next_word = next_word_match.group(1)
                                feminine = self._is_feminine_word(next_word)
                        
                        words = self.number_converter.number_to_words(number, feminine=feminine)
                        return words
                    else:
                        return number_str
            except (ValueError, OverflowError):
                return number_str
        
        # Pattern pour détecter les nombres entiers ET décimaux
        number_pattern = r'\b\d{1,6}(?:[.,]\d{1,3})?\b'
        
        result = re.sub(number_pattern, replace_number, text_with_ordinals)
        
        # Post-processing : corriger l'accord en genre pour les nombres composés
        # "vingt-et-un cuillère" → "vingt-et-une cuillère"
        result = self._fix_gender_agreement_in_numbers(result)
        
        return result
    
    def _fix_gender_agreement_in_numbers(self, text: str) -> str:
        """Corrige l'accord en genre dans les nombres composés"""
        # Pattern pour détecter "...-et-un [mot]" ou "...-un [mot]" ou "cent un [mot]" etc.
        patterns_to_fix = [
            r'(-et-un)\s+([a-zA-Zàâäéèêëïîôöùûüÿç]+)',  # vingt-et-un cuillère
            r'(cent\s+un)\s+([a-zA-Zàâäéèêëïîôöùûüÿç]+)',  # cent un cuillère
            r'(mille\s+un)\s+([a-zA-Zàâäéèêëïîôöùûüÿç]+)'   # mille un cuillère
        ]
        
        def replace_if_feminine(match):
            number_part = match.group(1)
            following_word = match.group(2)
            
            if self._is_feminine_word(following_word):
                if '-et-un' in number_part:
                    return number_part.replace('-et-un', '-et-une') + ' ' + following_word
                elif 'cent un' in number_part:
                    return number_part.replace('cent un', 'cent une') + ' ' + following_word
                elif 'mille un' in number_part:
                    return number_part.replace('mille un', 'mille une') + ' ' + following_word
            
            return match.group(0)  # Pas de changement si masculin
        
        result = text
        for pattern in patterns_to_fix:
            result = re.sub(pattern, replace_if_feminine, result)
        
        return result
    
    def _is_feminine_word(self, word):
        """Détermine si un mot français est probablement féminin basé sur ses terminaisons"""
        word = word.lower()
        
        # Terminaisons typiquement féminines
        feminine_endings = [
            'tion', 'sion', 'ance', 'ence', 'ette', 'elle', 'esse', 'ure',
            'ière', 'euse', 'rice', 'ade', 'aille', 'aine', 'erie', 'ie'
        ]
        
        # Mots féminins courants qui ne suivent pas les règles
        irregular_feminines = {
            'cuillère', 'cuillères', 'tasse', 'tasses', 'bouteille', 'bouteilles',
            'heure', 'heures', 'minute', 'minutes', 'seconde', 'secondes',
            'page', 'pages', 'personne', 'personnes', 'chose', 'choses',
            'fois', 'voiture', 'voitures', 'poussette', 'poussettes',
            'mongolfière', 'mongolfières', 'table', 'tables'
        }
        
        # Exceptions masculines (mots en -e qui sont masculins)
        masculine_exceptions = {
            'homme', 'hommes', 'livre', 'livres', 'groupe', 'groupes',
            'monde', 'mondes', 'nombre', 'nombres', 'membre', 'membres'
        }
        
        # Vérifier les exceptions masculines d'abord
        if word in masculine_exceptions:
            return False
            
        # Vérifier les mots irréguliers féminins
        if word in irregular_feminines:
            return True
            
        # Vérifier les terminaisons
        for ending in feminine_endings:
            if word.endswith(ending):
                return True
                
        # Si le mot se termine par 'e' et n'est pas dans les exceptions masculines
        if word.endswith('e') and len(word) > 3:
            return True
            
        return False
    
    def _expand_abbreviations(self, text: str) -> str:
        """Expanse les abréviations vers leurs formes complètes"""
        expansions = self.abbreviation_expansions.get(self.language_id, {})
        
        result = text
        
        # D'abord traiter les unités composées spéciales
        # km/h → kilomètres heure
        result = re.sub(r'\b(\d+(?:[.,]\d+)?)\s*km/h\b', r'\1 kilomètres heure', result, flags=re.IGNORECASE)
        
        # Températures avec contexte numérique
        result = re.sub(r'\b(\d+(?:[.,]\d+)?)\s*°C\b', r'\1 degrés', result, flags=re.IGNORECASE)
        result = re.sub(r'\b(\d+(?:[.,]\d+)?)\s*°F\b', r'\1 degrés', result, flags=re.IGNORECASE)
        result = re.sub(r'\b(\d+(?:[.,]\d+)?)\s*°\b', r'\1 degrés', result, flags=re.IGNORECASE)
        
        # Unités de mesure qui nécessitent un contexte numérique (précédées de chiffres)
        numeric_units = {
            'l': 'litres',
            'm': 'mètres',
            'g': 'grammes',
            'km': 'kilomètres',
            'mm': 'millimètres',
            'cm': 'centimètres',
            'kg': 'kilogrammes',
            'mg': 'milligrammes',
            'ml': 'millilitres'
        }
        
        # Traiter les unités avec contexte numérique (incluant décimaux)
        for unit, expansion in numeric_units.items():
            # Pattern: nombre (entier ou décimal) + espaces optionnels + unité + frontière de mot
            pattern = r'\b(\d+(?:[.,]\d+)?)\s*(' + re.escape(unit) + r')\b'
            result = re.sub(pattern, r'\1 ' + expansion, result, flags=re.IGNORECASE)
        
        # Ensuite traiter les autres abréviations normalement
        # Trier par longueur décroissante
        sorted_abbrevs = sorted(expansions.items(), key=lambda x: len(x[0]), reverse=True)
        
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
    
    def _fix_unit_spacing(self, text: str) -> str:
        """Corrige les espaces autour des unités de mesure"""
        # Unités composées à ne pas séparer
        compound_units = [
            'millimètres', 'centimètres', 'kilomètres',
            'milligrammes', 'kilogrammes',
            'millilitres', 'millimètres'
        ]
        
        # Ajouter un espace avant les unités simples qui en manquent
        simple_units = [
            'degrés', 'heures', 'minutes', 'secondes',
            'mètres', 'grammes', 'litres'
        ]
        
        for unit in simple_units:
            # Pattern pour détecter un mot suivi directement d'une unité
            # mais éviter les unités composées
            pattern = r'(?<![a-zA-Zàâäéèêëïîôöùûüÿç])([a-zA-Zàâäéèêëïîôöùûüÿç]+)(' + re.escape(unit) + r')'
            
            # Vérifier que ce n'est pas une unité composée
            def replacement(match):
                full_match = match.group(1) + match.group(2)
                if any(compound in full_match for compound in compound_units):
                    return match.group(0)  # Ne pas modifier
                return match.group(1) + ' ' + match.group(2)
            
            text = re.sub(pattern, replacement, text)
        
        return text
    
    def _clean_text_for_tts(self, text: str) -> str:
        """Nettoie le texte pour la synthèse vocale"""
        # Supprimer le contenu entre parenthèses
        text = re.sub(r'\([^)]*\)', '', text)
        
        # Supprimer formatage markdown
        text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)  # **gras** → gras
        text = re.sub(r'\*(.+?)\*', r'\1', text)      # *italique* → italique
        text = re.sub(r'`(.+?)`', r'\1', text)        # `code` → code
        
        # Supprimer les tirets de listes
        text = re.sub(r'^\s*-\s+', '', text, flags=re.MULTILINE)  # Début de ligne - espace
        text = re.sub(r'\n\s*-\s+', '\n', text)  # Tirets en milieu de texte
        
        # Supprimer TOUS les emojis (toutes les plages Unicode des emojis)
        emoji_pattern = r'[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF\U0001F1E0-\U0001F1FF\U0001F900-\U0001F9FF\U00002600-\U000026FF\U00002700-\U000027BF\U0001F190-\U0001F1FF\U0001FA70-\U0001FAFF\U00002300-\U000023FF\U00002B50\U00002B55\U00002728\U0001F004\U0001F0CF\U0001F170-\U0001F251\U0001F600-\U0001F636\U0001F681-\U0001F6C5\U0001F30D-\U0001F567]'
        text = re.sub(emoji_pattern, '', text)
        
        # Approche conservatrice : garder tout sauf les caractères vraiment indésirables
        # Supprimer seulement les caractères de contrôle et symboles problématiques
        text = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F-\x9F]', '', text)  # Caractères de contrôle
        text = re.sub(r'[©®™°²³¼½¾±×÷√∞≈≠≤≥◊]', '', text)  # Symboles mathématiques/spéciaux
        
        # Supprimer caractères de formatage indésirables (mais garder ` qui peut être une apostrophe)
        text = re.sub(r'[_~]', ' ', text)
        
        # Normaliser les espaces multiples
        text = re.sub(r'\s+', ' ', text)
        
        return text.strip()
    
    def cleanup(self):
        """Nettoyage des ressources"""
        if self.text_buffer.strip():
            logger.info(f"Buffer restant à la fin: {repr(self.text_buffer)}")
        
        logger.info(f"SentenceNormalizer '{self.name}' nettoyé")
