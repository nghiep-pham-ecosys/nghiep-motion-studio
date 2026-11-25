from process_sim_lab import Stream
from process_sim_lab.units import isothermal_cstr_first_order

feed = Stream(flows={"A": 10.0, "W": 50.0}, temperature=330.0)  # mol/s, K
r = isothermal_cstr_first_order(
    feed, reactant="A", product="B",
    rate_constant=0.01,     # 1/s
    volume=2.0,             # m3
    volumetric_flow=0.004,  # m3/s
)
print(r.space_time, r.damkohler, r.conversion)  # 500.0 5.0 0.8333...
print(r.outlet.flows)  # A 1.667, W 50.0, B 8.333 mol/s
