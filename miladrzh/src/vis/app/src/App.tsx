import React, { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import * as d3 from 'd3';
import './App.css';

type LogEvent = {
  turn_id?: number;
  type: 'tool_call' | 'prefill' | 'decode' | string;
  rel_start_ms?: number;
  rel_end_ms?: number;
  duration_ms?: number;
  prompt_tokens?: number;
  completion_tokens?: number;
  event_token?: number;
  total_token?: number;
  // tool_call specific
  tool_name?: string;
  tool_subtype?: string;
  // prefill/decode content
  message?: unknown;
  messages?: unknown;
  [k: string]: unknown;
};

type ExecutionLog = {
  trace_id?: string;
  agent_type?: string;
  benchmark?: string;
  task_id?: string;
  model?: string;
  final_answer?: string;
  total_wall_time_ms?: number;
  events?: LogEvent[];
  [k: string]: unknown;
};

function App() {
  const timelineRef = useRef<SVGSVGElement | null>(null);

  const [logs, setLogs] = useState<string[]>([]);
  const [selectedLog, setSelectedLog] = useState<string | null>(null);
  const [logContent, setLogContent] = useState<ExecutionLog | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [timelineWidth, setTimelineWidth] = useState<number>(0);

  const getEventTokens = (event: LogEvent): number => {
    const totalTokens = Number(event.total_token ?? 0);
    return totalTokens > 0 ? totalTokens : 0;
  };

  const getFinalAnswerPreview = (answer?: string, maxWords = 100): string => {
    if (!answer) return '-';
    const words = answer.trim().split(/\s+/).filter(Boolean);
    if (words.length <= maxWords) return words.join(' ');
    return `${words.slice(0, maxWords).join(' ')}…`;
  };

  const getLogLabel = (logFile: string): string => {
    const base = logFile.replace(/\.json$/i, '');
    const trimmed = base.replace(/^\d{8}T\d{6}_/, '');
    return trimmed.length > 0 ? trimmed : base;
  };

  // Update logsBaseUrl to use relative path for serverless setup
  const logsBaseUrl = '/logs';

  useEffect(() => {
    fetch(`${logsBaseUrl}/index.json`)
      .then((response) => {
        if (!response.ok) throw new Error('Log index not found');
        return response.json();
      })
      .then((data) => {
        if (Array.isArray(data)) {
          setLogs(data as string[]);
        } else if (Array.isArray(data?.logs)) {
          setLogs(data.logs as string[]);
        } else {
          setLogs([]);
        }
      })
      .catch((err) => {
        console.error('Error fetching log index:', err);
        setLogs([]);
      });
  }, []);

  useEffect(() => {
    if (!selectedLog && logs.length > 0) {
      setSelectedLog(logs[0]);
      return;
    }
    if (selectedLog && logs.length > 0 && !logs.includes(selectedLog)) {
      setSelectedLog(logs[0]);
    }
  }, [logs, selectedLog]);

  // Add a function to validate and adapt the schema of log files
  const adaptLogSchema = (log: ExecutionLog): ExecutionLog => {
    // Ensure trace_id follows a consistent format
    if (log.trace_id && !log.trace_id.startsWith('2026')) {
      log.trace_id = `2026${log.trace_id}`; // Prepend year if missing
    }

    // Add other schema adaptations as needed
    return log;
  };

  // Update the fetch logic to handle missing files gracefully
  useEffect(() => {
    if (selectedLog) {
      // Fetch the content of the selected log
      setError(null);
      setLogContent(null);
      fetch(`${logsBaseUrl}/${selectedLog}`)
        .then((response) => {
          if (!response.ok) {
            if (response.status === 404) {
              throw new Error(`Log file not found: ${selectedLog}`);
            }
            throw new Error('Network response was not ok');
          }
          return response.json();
        })
        .then((data) => setLogContent(adaptLogSchema(data)))
        .catch((e) => {
          console.error('Error fetching log:', e);
          setError(e.message);
        });
    }
  }, [selectedLog]);

  useLayoutEffect(() => {
    const node = timelineRef.current;
    if (!node) return;

    const ro = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect?.width ?? 0;
      // avoid thrashing for tiny diffs
      setTimelineWidth(Math.max(0, Math.floor(w)));
    });
    ro.observe(node);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    const events = logContent?.events;
    if (events && events.length > 0 && timelineWidth > 0) {
      // Create the timeline visualization
      const outerW = timelineWidth;
      const outerH = 520;
      const margin = { top: 16, right: 20, bottom: 36, left: 56 };
      const w = outerW - margin.left - margin.right;
      const h = outerH - margin.top - margin.bottom;

      const svg = d3
        .select(timelineRef.current)
        .attr('width', outerW)
        .attr('height', outerH);

      svg.selectAll('*').remove(); // Clear previous content

      const g = svg
        .append('g')
        .attr('transform', `translate(${margin.left},${margin.top})`);

      const xMax = d3.max(events, (d) => Number(d.rel_end_ms ?? d.rel_start_ms ?? 0)) ?? 0;
      const yMax = d3.max(events, (d) => getEventTokens(d)) ?? 0;

      const xScale = d3.scaleLinear()
        .domain([0, xMax])
        .range([0, w]);

      const yScale = d3.scaleLinear()
        .domain([0, yMax])
        .nice()
        .range([h, 0]);

      const colorScale = (type: string) => {
        switch (type) {
          case 'tool_call': return '#9aa4b2';
          case 'prefill': return '#2ecc71';
          case 'decode': return '#4aa3ff';
          default: return '#e5e7eb';
        }
      };

      // Axes
      const xAxis = g
        .append('g')
        .attr('transform', `translate(0,${h})`)
        .call(d3.axisBottom(xScale).ticks(10).tickFormat((d) => `${d}ms`));

  const yAxis = g.append('g').call(d3.axisLeft(yScale).ticks(10));

      // Axis styling for dark theme
      xAxis.selectAll('path, line').attr('stroke', 'rgba(255,255,255,0.22)');
      xAxis.selectAll('text').attr('fill', 'rgba(255,255,255,0.72)');
      yAxis.selectAll('path, line').attr('stroke', 'rgba(255,255,255,0.22)');
      yAxis.selectAll('text').attr('fill', 'rgba(255,255,255,0.72)');

  // Bigger axis text
  xAxis.selectAll('text').style('font-size', '12px');
  yAxis.selectAll('text').style('font-size', '12px');

      g.append('text')
        .attr('x', w / 2)
        .attr('y', h + margin.bottom - 6)
        .attr('text-anchor', 'middle')
        .attr('fill', 'rgba(255,255,255,0.65)')
        .style('font-size', '14px')
        .style('font-weight', 700)
        .text('time (ms)');

      g.append('text')
        .attr('x', -h / 2)
        .attr('y', -margin.left + 14)
        .attr('transform', 'rotate(-90)')
        .attr('text-anchor', 'middle')
        .attr('fill', 'rgba(255,255,255,0.65)')
        .style('font-size', '16px')
        .style('font-weight', 800)
        .text('# tokens');

      // Bars
      const minBarH = 6;
      g.selectAll('rect')
        .data(events)
        .enter()
        .append('rect')
        .attr('x', (d) => xScale(Number(d.rel_start_ms ?? 0)))
        .attr('y', (d) => {
          const yVal = getEventTokens(d);
          return yScale(yVal);
        })
        .attr('width', (d) => {
          const duration = Number(
            (d.rel_end_ms ?? d.rel_start_ms ?? 0) - (d.rel_start_ms ?? 0)
          );
          return Math.max(2, xScale(duration) - xScale(0));
        })
        .attr('height', (d) => {
          const yVal = getEventTokens(d);
          const height = h - yScale(yVal);
          return Math.max(minBarH, height);
        })
        .attr('rx', 3)
        .attr('ry', 3)
        .attr('fill', (d) => colorScale(String(d.type)))
        .attr('opacity', 0.9)
        .on('mouseover', (event: MouseEvent, d) => {
          const tooltip = d3.select('#tooltip');
          const tokenParts = d.total_token !== undefined ? `total_token=${d.total_token}` : 'total_token=-';
          tooltip
            .style('visibility', 'visible')
            .text(
              `type=${String(d.type)} start=${d.rel_start_ms ?? '?'}ms end=${d.rel_end_ms ?? '?'}ms ${tokenParts}${d.tool_name ? ` tool=${d.tool_name}` : ''}`
            );
        })
        .on('mousemove', (event: MouseEvent) => {
          const tooltip = d3.select('#tooltip');
          tooltip
            .style('top', `${event.pageY + 10}px`)
            .style('left', `${event.pageX + 10}px`);
        })
        .on('mouseout', () => {
          const tooltip = d3.select('#tooltip');
          tooltip.style('visibility', 'hidden');
        });
    } else {
      // Clear svg if we don't have data
      const svg = d3.select(timelineRef.current);
      svg.selectAll('*').remove();
    }
  }, [logContent, timelineWidth]);

  return (
    <div className="App">
      <div className="navbar">
        <h2>Logs</h2>
        <ul>
          {logs.map((log) => (
            <li
              key={log}
              role="button"
              tabIndex={0}
              aria-pressed={selectedLog === log}
              className={selectedLog === log ? 'is-active' : undefined}
              onClick={() => setSelectedLog(log)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') setSelectedLog(log);
              }}
            >
              {getLogLabel(log)}
            </li>
          ))}
        </ul>
      </div>
      <div className="main-frame">
        <h2>Timeline</h2>
        <div className="content-wrap">
          {error ? (
            <div className="error-message">{error}</div>
          ) : (
            <>
              <div className="log-meta">
                <div className="log-meta__title">Log metadata</div>
                <div className="log-meta__grid">
                  <div className="log-meta__label">Agent type</div>
                  <div className="log-meta__value is-strong">{logContent?.agent_type ?? '-'}</div>

                  <div className="log-meta__label">Benchmark</div>
                  <div className="log-meta__value is-strong">{logContent?.benchmark ?? '-'}</div>

                  <div className="log-meta__label">Task id</div>
                  <div className="log-meta__value is-strong">{logContent?.task_id ?? '-'}</div>

                  <div className="log-meta__label">Model</div>
                  <div className="log-meta__value is-strong">{logContent?.model ?? '-'}</div>

                  <div className="log-meta__label">Total wall time</div>
                  <div className="log-meta__value is-strong">
                    {typeof logContent?.total_wall_time_ms === 'number'
                      ? `${logContent.total_wall_time_ms} ms`
                      : '-'}
                  </div>

                  <div className="log-meta__label">Final answer (first 100 words)</div>
                  <div className="log-meta__value">
                    {getFinalAnswerPreview(logContent?.final_answer)}
                  </div>
                </div>

                <div className="log-meta__debug">
                  <span>selected: {selectedLog ?? '(none)'}</span>
                  <span>trace_id: {logContent?.trace_id ?? '-'}</span>
                  <span>events: {logContent?.events?.length ?? 0}</span>
                  {error ? <span className="log-meta__error">error: {error}</span> : null}
                </div>
              </div>

              <div className="legend" aria-label="Legend">
                <div className="legend__title">Legend</div>
                <div className="legend__items">
                  <div className="legend__item">
                    <span className="legend__swatch legend__swatch--tool" />
                    <span className="legend__label">tool call</span>
                  </div>
                  <div className="legend__item">
                    <span className="legend__swatch legend__swatch--prefill" />
                    <span className="legend__label">prefill</span>
                  </div>
                  <div className="legend__item">
                    <span className="legend__swatch legend__swatch--decode" />
                    <span className="legend__label">decode</span>
                  </div>
                </div>
              </div>

              <svg id="timeline" ref={timelineRef}></svg>
              <div id="tooltip" className="tooltip"></div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

export default App;
