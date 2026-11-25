def isothermal_cstr_first_order(feed, *, reactant, product,
                                rate_constant, volume, volumetric_flow):
    if rate_constant < 0:
        raise ValueError("rate_constant must be >= 0")
    if volume <= 0:
        raise ValueError("volume must be > 0")
    if volumetric_flow <= 0:
        raise ValueError("volumetric_flow must be > 0")
    if reactant not in feed.flows:
        raise ValueError(f"feed has no component {reactant!r}")
    if reactant == product:
        raise ValueError("reactant and product must differ")

    tau = volume / volumetric_flow
    damkohler = rate_constant * tau
    conversion = damkohler / (1.0 + damkohler)

    fa0 = feed.flows[reactant]
    reacted = fa0 * conversion
    flows = dict(feed.flows)
    flows[reactant] = fa0 - reacted
    flows[product] = flows.get(product, 0.0) + reacted

    outlet = Stream(flows=flows, temperature=feed.temperature,
                    pressure=feed.pressure)
    return CSTRResult(outlet=outlet, conversion=conversion,
                      space_time=tau, damkohler=damkohler)
