import React from 'react';
import {createRoot} from 'react-dom/client';
import App from './App';
import LauraTheme from './LauraTheme';
import './styles.css';
import './shell.css';
import './visual.css';
createRoot(document.getElementById('root')!).render(<React.StrictMode><LauraTheme><App/></LauraTheme></React.StrictMode>);
