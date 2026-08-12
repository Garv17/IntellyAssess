import type { AnswerSave, Question, Section } from '../api';

type AnswerMap = Record<string, AnswerSave>;

/** Palette of every question in the exam — answered/remaining counts, a
    jump-to-question grid grouped by section, and the answered/review legend.
    Shared by the standard MCQ/DI layout and the coding 3-pane workspace so
    both always show the exact same navigation state. */
export default function QuestionNavigator({
  sections,
  flat,
  cursor,
  answers,
  answered,
  onSelect,
}: {
  sections: Section[];
  flat: { section: Section; question: Question }[];
  cursor: number;
  answers: AnswerMap;
  answered: number;
  onSelect: (index: number) => void;
}) {
  return (
    <>
      <div className="palette-stats">
        <div>
          <strong>{answered}</strong>
          <span>Answered</span>
        </div>
        <div>
          <strong>{flat.length - answered}</strong>
          <span>Remaining</span>
        </div>
      </div>
      {sections.map((section) => (
        <div key={section.id} className="palette-section">
          <h4>{section.title}</h4>
          <div className="palette-grid">
            {section.questions.map((question) => {
              const index = flat.findIndex((f) => f.question.id === question.id);
              const answer = answers[question.id];
              const isAnswered = Boolean(answer?.selected_option_id || answer?.code_text?.trim());
              const classes = [
                'palette-cell',
                isAnswered ? 'answered' : '',
                answer?.is_marked_for_review ? 'review' : '',
                index === cursor ? 'active' : '',
              ]
                .filter(Boolean)
                .join(' ');
              return (
                <button
                  key={question.id}
                  className={classes}
                  onClick={() => onSelect(index)}
                  title={question.type.toUpperCase()}
                >
                  {index + 1}
                </button>
              );
            })}
          </div>
        </div>
      ))}
      <div className="legend">
        <span>
          <i className="swatch answered" /> Answered
        </span>
        <span>
          <i className="swatch review" /> For review
        </span>
        <span>
          <i className="swatch" /> Not answered
        </span>
      </div>
    </>
  );
}
