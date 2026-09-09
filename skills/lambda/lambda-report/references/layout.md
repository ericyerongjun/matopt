# Report layout defaults

## Tables

Use `tabularx` with `\textwidth`. One flexible text column (`Y` or `L`), compact numeric columns.

```latex
\begin{table}[H]
\centering
\small
\caption{District-level summary}
\begin{tabularx}{\textwidth}{lrrrrY}
\toprule
District & PM2.5 & PM10 & NO2 & AQI & Interpretation \\
\midrule
Mong Kok & 54.7 & 86.2 & 67.9 & 111.7 & Highest average exposure. \\
\bottomrule
\end{tabularx}
\end{table}
```

Wide numeric tables: `\resizebox{\textwidth}{!}` or `longtable`. Prefer fewer columns over overflow.

## Figures

```latex
\begin{figure}[H]
  \centering
  \includegraphics[width=\textwidth]{figures/price_by_area.png}
  \caption{What the reader should notice, with a number.}
\end{figure}
```

Use `0.82\textwidth` for small diagnostics. Local workspace paths only — never OSS URLs.
