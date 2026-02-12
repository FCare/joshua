"""
Convertisseur de nombres en lettres pour le TTS - adapté du SOCServer
Module séparé pour éviter les imports circulaires
"""
import re


class NumberToWordsConverter:
    """Convertisseur de nombres en lettres pour français et anglais"""
    
    def __init__(self, language='fr'):
        self.language = language.lower()[:2]  # 'fr' ou 'en'
        
        # Dictionnaires français
        self.fr_units = ['', 'un', 'deux', 'trois', 'quatre', 'cinq', 'six', 'sept', 'huit', 'neuf']
        self.fr_teens = ['dix', 'onze', 'douze', 'treize', 'quatorze', 'quinze', 'seize', 
                         'dix-sept', 'dix-huit', 'dix-neuf']
        self.fr_tens = ['', '', 'vingt', 'trente', 'quarante', 'cinquante', 'soixante', 
                        'soixante-dix', 'quatre-vingt', 'quatre-vingt-dix']
        self.fr_scales = ['', 'mille', 'million', 'milliard']
        
        # Dictionnaires anglais
        self.en_units = ['', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine']
        self.en_teens = ['ten', 'eleven', 'twelve', 'thirteen', 'fourteen', 'fifteen', 'sixteen',
                         'seventeen', 'eighteen', 'nineteen']
        self.en_tens = ['', '', 'twenty', 'thirty', 'forty', 'fifty', 'sixty', 'seventy', 
                        'eighty', 'ninety']
        self.en_scales = ['', 'thousand', 'million', 'billion']
    
    def number_to_words(self, num, feminine=False):
        """Convertit un nombre entier en lettres avec gestion du genre"""
        if num == 0:
            return 'zéro' if self.language == 'fr' else 'zero'
        
        if num > 1000000000000:  # Plus de mille milliards
            return 'plus de mille milliards' if self.language == 'fr' else 'more than one trillion'
        
        if self.language == 'fr':
            return self._number_to_words_fr(num, feminine)
        else:
            return self._number_to_words_en(num)
    
    def _number_to_words_fr(self, num, feminine=False):
        """Convertit un nombre en français avec gestion du genre"""
        if num == 0:
            return 'zéro'
        
        if num < 0:
            return f"moins {self._number_to_words_fr(-num, feminine)}"
        
        chunks = []
        chunk_count = 0
        
        while num > 0:
            chunk = num % 1000
            if chunk != 0:
                # Transmettre le paramètre feminine seulement pour le chunk des unités (chunk_count == 0)
                chunk_name = self._chunk_to_words_fr(chunk, feminine=(chunk_count == 0 and feminine))
                if chunk_count == 0:
                    chunks.append(chunk_name)
                elif chunk_count == 1:
                    if chunk == 1:
                        chunks.append('mille')
                    else:
                        chunks.append(f"{chunk_name} mille")
                else:
                    scale = self.fr_scales[chunk_count]
                    if chunk == 1:
                        chunks.append(f"un {scale}")
                    else:
                        chunks.append(f"{chunk_name} {scale}s")
            
            num //= 1000
            chunk_count += 1
        
        return ' '.join(reversed(chunks))
    
    def _chunk_to_words_fr(self, num, feminine=False):
        """Convertit un chunk de 3 chiffres en français avec gestion du genre"""
        words = []
        
        # Centaines
        if num >= 100:
            hundreds = num // 100
            if hundreds == 1:
                words.append('cent')
            else:
                words.append(f"{self.fr_units[hundreds]} cent")
            num %= 100
            if num == 0 and hundreds > 1:
                words[-1] += 's'
        
        # Dizaines et unités
        if num >= 20:
            tens = num // 10
            units = num % 10
            if tens == 7 or tens == 9:
                words.append(self.fr_tens[tens - 1] if tens == 7 else self.fr_tens[8])
                if units != 0:
                    if units == 1 and tens == 7:
                        words.append('et onze')
                    elif units == 1 and tens == 9:
                        words.append('et onze')
                    else:
                        words.append(self.fr_teens[units])
                else:
                    words.append('dix')
            else:
                if units == 0:
                    words.append(self.fr_tens[tens])
                    if tens == 8:
                        words[-1] += 's'
                elif units == 1 and tens in [2, 3, 4, 5, 6]:
                    words.append(f"{self.fr_tens[tens]}-et-{self.fr_units[units]}")
                else:
                    words.append(f"{self.fr_tens[tens]}-{self.fr_units[units]}")
        elif num >= 10:
            words.append(self.fr_teens[num - 10])
        elif num > 0:
            if num == 1 and feminine:
                words.append('une')
            else:
                words.append(self.fr_units[num])
        
        return ' '.join(words)
    
    def _number_to_words_en(self, num):
        """Convertit un nombre en anglais"""
        if num == 0:
            return 'zero'
        
        if num < 0:
            return f"minus {self._number_to_words_en(-num)}"
        
        chunks = []
        chunk_count = 0
        
        while num > 0:
            chunk = num % 1000
            if chunk != 0:
                chunk_name = self._chunk_to_words_en(chunk)
                if chunk_count == 0:
                    chunks.append(chunk_name)
                else:
                    scale = self.en_scales[chunk_count]
                    chunks.append(f"{chunk_name} {scale}")
            
            num //= 1000
            chunk_count += 1
        
        return ' '.join(reversed(chunks))
    
    def _chunk_to_words_en(self, num):
        """Convertit un chunk de 3 chiffres en anglais"""
        words = []
        
        # Centaines
        if num >= 100:
            hundreds = num // 100
            words.append(f"{self.en_units[hundreds]} hundred")
            num %= 100
        
        # Dizaines et unités
        if num >= 20:
            tens = num // 10
            units = num % 10
            if units == 0:
                words.append(self.en_tens[tens])
            else:
                words.append(f"{self.en_tens[tens]}-{self.en_units[units]}")
        elif num >= 10:
            words.append(self.en_teens[num - 10])
        elif num > 0:
            words.append(self.en_units[num])
        
        return ' '.join(words)
    
    def _convert_ordinals(self, text):
        """Convertit les ordinaux français (1er, 2e, 2ème) en lettres"""
        if self.language == 'fr':
            # Pattern pour les ordinaux français
            pattern = r'\b(\d+)(er|e|ème)\b'
            
            def replace_ordinal(match):
                num = int(match.group(1))
                suffix = match.group(2)
                
                if num == 1:
                    return 'premier' if suffix == 'er' else 'première' 
                else:
                    word = self.number_to_words(num)
                    if word.endswith('e'):
                        return word[:-1] + 'ième'
                    elif word.endswith('f'):
                        return word[:-1] + 'vième'
                    else:
                        return word + 'ième'
            
            text = re.sub(pattern, replace_ordinal, text)
        
        return text