import { type AnswerSave, type Question } from '../api';

// Coding questions get their own IDE-style 3-pane workspace (see
// CodingWorkspace + CodingProblemPane/CodingEditorPane) driven directly by
// Exam.tsx, which needs to place the question navigator as a third pane
// alongside the problem/editor — something this single-pane component has no
// way to express. This component only ever renders MCQ/DI questions now.
export default function QuestionView({
  question,
  diGroup,
  answer,
  onChange,
}: {
  question: Question;
  diGroup?: { title: string; passage_md: string | null; image_url: string | null };
  answer?: AnswerSave;
  onChange: (id: string, patch: Partial<AnswerSave>, immediate: boolean) => void;
}) {
  return (
    <div>
      {diGroup && (
        <div className="di-stimulus">
          <h4>{diGroup.title}</h4>
          {diGroup.image_url && (
            <img src={diGroup.image_url} alt={diGroup.title} className="di-image" />
          )}
          {diGroup.passage_md && <pre className="di-passage">{diGroup.passage_md}</pre>}
        </div>
      )}
      <p className="question-body">{question.body_md}</p>
      <div className="options">
        {question.options.map((option, index) => (
          <label
            key={option.id}
            className={`option ${answer?.selected_option_id === option.id ? 'selected' : ''}`}
          >
            <input
              type="radio"
              name={question.id}
              checked={answer?.selected_option_id === option.id}
              // MCQ selections save immediately: they're discrete, cheap, and the
              // most painful thing to lose.
              onChange={() => onChange(question.id, { selected_option_id: option.id }, true)}
            />
            <span className="option-letter">{String.fromCharCode(65 + index)}</span>
            <span>{option.body}</span>
          </label>
        ))}
      </div>
      {answer?.selected_option_id && (
        <button
          className="btn link"
          onClick={() => onChange(question.id, { selected_option_id: null }, true)}
        >
          Clear response
        </button>
      )}
    </div>
  );
}
