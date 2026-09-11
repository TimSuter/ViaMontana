const languageChoice = document.querySelector('#language-choice');
const languageNote = document.querySelector('#language-note');
const preferenceKey = 'viamontana-language';
const languages = new Set(['en', 'de', 'fr', 'it']);

function updateLanguageNote() {
  languageNote.hidden = languageChoice.value === 'en';
  languageNote.textContent = languageNote.hidden
    ? ''
    : 'Translations are coming soon. The site is currently in English.';
}

try {
  const savedLanguage = localStorage.getItem(preferenceKey);
  if (languages.has(savedLanguage)) languageChoice.value = savedLanguage;
} catch {
  // The selector still works when browser storage is unavailable.
}
updateLanguageNote();

languageChoice.addEventListener('change', () => {
  try {
    localStorage.setItem(preferenceKey, languageChoice.value);
  } catch {
    // Keep the current choice for this page even without persistent storage.
  }
  updateLanguageNote();
});
