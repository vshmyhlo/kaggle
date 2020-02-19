from config2 import Config as C

config = C(
    seed=42,
    epochs=10,
    model=C(
        type='efficientnet-b0'),
    train=C(
        batch_size=128,
        optimizer=C(
            type='sgd',
            lr=0.16,
            momentum=0.9,
            weight_decay=1e-4,
            lookahead=C(
                lr=0.5,
                steps=5)),
        scheduler=C(
            type='coswarm')),
    eval=C(
        batch_size=128))
