import '@/i18n/i18n';
import '@/index.css';

import ReactDOM from 'react-dom/client';

import App from '@/App';

const initialTheme = localStorage.getItem('theme');
const systemDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
document.documentElement.classList.toggle(
  'dark',
  initialTheme === 'dark' ||
    (initialTheme === 'system' && systemDark) ||
    (!initialTheme && systemDark),
);

ReactDOM.createRoot(document.getElementById('root')!).render(<App />);
