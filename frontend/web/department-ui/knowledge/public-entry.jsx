import React from 'react';
import {createRoot} from 'react-dom/client';
import KnowledgeUnavailable from './KnowledgeUnavailable.jsx';
import './public-share.css';

// A historical share URL must not read a token, load a file or display a questionnaire.
createRoot(document.getElementById('knowledge-public-root')).render(<KnowledgeUnavailable />);
