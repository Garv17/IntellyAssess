import { Award, BarChart3, Download, Layers, TrendingDown, TrendingUp, Users } from 'lucide-react';
import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { api, downloadResults, type Analytics as AnalyticsData } from '../../api';
import { SkeletonCard, SkeletonStatRow } from '../../components/Skeleton';

export default function Analytics() {
  const { examId = '' } = useParams();
  const [data, setData] = useState<AnalyticsData | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .analytics(examId)
      .then(setData)
      .catch((err) => setError(err instanceof Error ? err.message : 'Failed to load analytics'));
  }, [examId]);

  const maxBucket = data ? Math.max(1, ...Object.values(data.score_buckets)) : 1;

  return (
    <div className="shell">
      <header className="top-bar">
        <strong>
          <Link to="/admin" className="btn link">
            ← Exams
          </Link>{' '}
          Analytics
        </strong>
        <button className="btn" onClick={() => void downloadResults(examId)}>
          <Download size={15} /> Export results
        </button>
      </header>

      <main className="container wide">
        {error && <div className="banner error">{error}</div>}
        {!data && !error && (
          <>
            <SkeletonStatRow count={6} />
            <SkeletonCard />
          </>
        )}

        {data && (
          <>
            <div className="stat-row">
              <div className="stat">
                <div className="stat-icon">
                  <Users size={17} />
                </div>
                <strong>{data.submissions}</strong>
                <span>Submissions</span>
              </div>
              <div className="stat active">
                <div className="stat-icon">
                  <BarChart3 size={17} />
                </div>
                <strong>{data.average_score ?? '—'}</strong>
                <span>Average</span>
              </div>
              <div className="stat">
                <div className="stat-icon">
                  <Layers size={17} />
                </div>
                <strong>{data.median_score ?? '—'}</strong>
                <span>Median</span>
              </div>
              <div className="stat ok">
                <div className="stat-icon">
                  <TrendingUp size={17} />
                </div>
                <strong>{data.highest_score ?? '—'}</strong>
                <span>Highest</span>
              </div>
              <div className="stat">
                <div className="stat-icon">
                  <TrendingDown size={17} />
                </div>
                <strong>{data.lowest_score ?? '—'}</strong>
                <span>Lowest</span>
              </div>
              <div className="stat">
                <div className="stat-icon">
                  <Award size={17} />
                </div>
                <strong>{data.max_score ?? '—'}</strong>
                <span>Out of</span>
              </div>
            </div>

            <section className="card">
              <div className="card-header">
                <h2>Score distribution</h2>
              </div>
              <div className="bars">
                {Object.entries(data.score_buckets).map(([bucket, count]) => (
                  <div key={bucket} className="bar-row">
                    <span className="bar-label">{bucket}</span>
                    <div className="bar-track">
                      <div
                        className="bar-fill"
                        style={{ width: `${(count / maxBucket) * 100}%` }}
                      />
                    </div>
                    <span className="bar-value">{count}</span>
                  </div>
                ))}
              </div>
            </section>

            <section className="card">
              <div className="card-header">
                <h2>Question performance</h2>
              </div>
              <p className="muted small">
                Low accuracy on a question with high attempts usually means the question is
                flawed, not that the cohort is weak. Worth reviewing before results go out.
              </p>
              <div className="table-wrap">
                <table className="table">
                  <thead>
                    <tr>
                      <th>Question</th>
                      <th>Type</th>
                      <th>Attempted</th>
                      <th>Correct</th>
                      <th>Accuracy</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.question_stats.map((stat) => (
                      <tr key={stat.question_id}>
                        <td className="preview">{stat.body_preview}</td>
                        <td>
                          <span className="tag">{stat.type}</span>
                        </td>
                        <td>{stat.attempted}</td>
                        <td>{stat.correct}</td>
                        <td>
                          <span
                            className={`badge ${
                              stat.accuracy < 20 && stat.attempted > 5
                                ? 'badge-danger'
                                : stat.accuracy >= 70
                                  ? 'badge-success'
                                  : 'badge-warning'
                            }`}
                          >
                            {stat.accuracy}%
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          </>
        )}
      </main>
    </div>
  );
}
