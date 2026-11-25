def retrieve(query, index, k=3):
    """Return the k passages closest to the query."""
    vector = embed(query)
    hits = index.search(vector, k)
    return [hit.text for hit in hits]


def answer(query, index):
    context = retrieve(query, index)
    prompt = build_prompt(query, context)
    return generate(prompt)
