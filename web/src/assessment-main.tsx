import ReactDOM from 'react-dom/client';
import AssessmentApp from './AssessmentApp';
import { consumeLaunchToken, createAssessmentApi } from './assessment-api';
import './styles.css';
import './assessment.css';

// Consume synchronously, outside React rendering/effects, before any API request.
const token = consumeLaunchToken(window.location, window.history);
const api = token ? createAssessmentApi(token) : null;
const root = document.getElementById('root');
if (!root) throw new Error('Missing assessment root');
ReactDOM.createRoot(root).render(<AssessmentApp api={api} />);
