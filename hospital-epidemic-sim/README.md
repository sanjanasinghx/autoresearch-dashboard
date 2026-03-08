# Hospital Network Epidemic Simulation

Interactive web-based SIR epidemic simulation on the US Hospital Referral Region (HRR) network.

## Quick Start

Just open `hospital_epidemic_sim.html` in any browser — no server, no install needed.

## What it does

- **304 HRR nodes** positioned at real US geographic coordinates
- **767 edges** from the Dartmouth Atlas hospital referral network
- **Stochastic SIR spreading** (β=0.08, γ=0.05 — adjustable via sliders)
- Click any node to seed an outbreak
- Choose sentinel surveillance strategy: Random / Degree Hubs / Betweenness / Community-based
- Live infection curves with detection day annotation
- Real-time stats: detection delay, total infected, amplification risk

## Data Sources

| File | Description |
|---|---|
| `hrr_centrality.csv` | Node attributes: degree, betweenness, eigenvector centrality, community, hospitals, beds |
| `hrr_edges.csv` | Network edges (HRR-to-HRR patient referral links) |
| `pareto_results.csv` | Pre-computed detection time vs amplification risk for each strategy |

Data from the [Dartmouth Atlas of Health Care](https://www.dartmouthatlas.org/).

## Sentinel Strategies

| Strategy | Logic |
|---|---|
| Random | Uniformly random selection |
| Degree Hubs | Top-k nodes by degree centrality |
| Betweenness | Top-k nodes by betweenness centrality |
| Community | One hub per detected community + fill by degree |
