/** Same bundled public Plotly API imported by react-plotly.js; no second Plotly bundle. */
declare module "plotly.js/dist/plotly" {
  const Plotly: { Plots: { resize(graph: HTMLElement): Promise<unknown> } };
  export default Plotly;
}
