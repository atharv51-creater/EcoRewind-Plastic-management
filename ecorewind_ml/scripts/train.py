"""
Train a 4-class classifier: plastic_bottle / plastic_bag / aluminium_can / other_invalid.
Transfer learning with MobileNetV2 (small and fast enough for a kiosk or the browser).

Usage:
    python scripts/train.py --data data/dataset --out outputs
"""
import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import tensorflow as tf
from sklearn.metrics import classification_report, confusion_matrix
from sklearn.utils.class_weight import compute_class_weight
from tensorflow import keras
from tensorflow.keras import layers

IMG = 224
SEED = 42


def load(path, batch, shuffle):
    return keras.utils.image_dataset_from_directory(
        path, image_size=(IMG, IMG), batch_size=batch,
        label_mode="int", shuffle=shuffle, seed=SEED)


def build_model(n_classes):
    aug = keras.Sequential([
        layers.RandomFlip("horizontal"),
        layers.RandomRotation(0.08),
        layers.RandomZoom(0.15),
        layers.RandomContrast(0.2),
        layers.RandomBrightness(0.2),
    ], name="augmentation")

    base = keras.applications.MobileNetV2(input_shape=(IMG, IMG, 3), include_top=False, weights="imagenet")
    base.trainable = False

    inp = keras.Input((IMG, IMG, 3))
    x = aug(inp)
    x = layers.Rescaling(1 / 127.5, offset=-1)(x)  # MobileNetV2 expects [-1, 1]
    x = base(x, training=False)
    x = layers.GlobalAveragePooling2D()(x)
    x = layers.Dropout(0.3)(x)
    out = layers.Dense(n_classes, activation="softmax")(x)
    return keras.Model(inp, out), base


def plot_history(histories, path):
    acc = sum([h.history["accuracy"] for h in histories], [])
    val = sum([h.history["val_accuracy"] for h in histories], [])
    loss = sum([h.history["loss"] for h in histories], [])
    vloss = sum([h.history["val_loss"] for h in histories], [])
    fig, ax = plt.subplots(1, 2, figsize=(11, 4))
    ax[0].plot(acc, label="train"); ax[0].plot(val, label="val"); ax[0].set_title("Accuracy"); ax[0].legend()
    ax[1].plot(loss, label="train"); ax[1].plot(vloss, label="val"); ax[1].set_title("Loss"); ax[1].legend()
    for a in ax:
        a.set_xlabel("epoch")
    fig.tight_layout(); fig.savefig(path, dpi=140); plt.close(fig)


def plot_confusion(cm, names, path):
    fig, ax = plt.subplots(figsize=(6, 5))
    ax.imshow(cm, cmap="Greens")
    ax.set_xticks(range(len(names))); ax.set_yticks(range(len(names)))
    ax.set_xticklabels(names, rotation=30, ha="right"); ax.set_yticklabels(names)
    for i in range(len(names)):
        for j in range(len(names)):
            ax.text(j, i, cm[i, j], ha="center", va="center",
                    color="white" if cm[i, j] > cm.max() / 2 else "black")
    ax.set_xlabel("Predicted"); ax.set_ylabel("Actual"); ax.set_title("Confusion matrix (test set)")
    fig.tight_layout(); fig.savefig(path, dpi=140); plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/dataset")
    ap.add_argument("--out", default="outputs")
    ap.add_argument("--batch", type=int, default=32)
    ap.add_argument("--epochs-head", type=int, default=10)
    ap.add_argument("--epochs-finetune", type=int, default=10)
    args = ap.parse_args()

    tf.keras.utils.set_random_seed(SEED)
    data, out = Path(args.data), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    train_ds = load(data / "train", args.batch, True)
    val_ds = load(data / "val", args.batch, False)
    test_ds = load(data / "test", args.batch, False)
    names = train_ds.class_names
    print("Classes:", names)
    json.dump(names, open(out / "labels.json", "w"))

    # class weights from the training files (handles imbalance)
    y_train = np.array([names.index(Path(p).parent.name) for p in train_ds.file_paths])
    cw = compute_class_weight("balanced", classes=np.arange(len(names)), y=y_train)
    class_weight = {i: float(w) for i, w in enumerate(cw)}
    print("Class weights:", class_weight)

    AUTOTUNE = tf.data.AUTOTUNE
    train_ds = train_ds.cache().prefetch(AUTOTUNE)
    val_ds = val_ds.cache().prefetch(AUTOTUNE)
    test_pf = test_ds.prefetch(AUTOTUNE)

    model, base = build_model(len(names))
    callbacks = [
        keras.callbacks.EarlyStopping(patience=4, restore_best_weights=True, monitor="val_accuracy"),
        keras.callbacks.ReduceLROnPlateau(patience=2, factor=0.4, monitor="val_loss"),
        keras.callbacks.ModelCheckpoint(str(out / "best.keras"), save_best_only=True, monitor="val_accuracy"),
    ]

    # Phase 1: train only the new classification head
    model.compile(optimizer=keras.optimizers.Adam(1e-3),
                  loss="sparse_categorical_crossentropy", metrics=["accuracy"])
    h1 = model.fit(train_ds, validation_data=val_ds, epochs=args.epochs_head,
                   class_weight=class_weight, callbacks=callbacks)

    # Phase 2: fine-tune the top of the backbone at a low learning rate
    base.trainable = True
    for layer in base.layers[:-30]:
        layer.trainable = False
    for layer in base.layers:
        if isinstance(layer, layers.BatchNormalization):
            layer.trainable = False
    model.compile(optimizer=keras.optimizers.Adam(1e-5),
                  loss="sparse_categorical_crossentropy", metrics=["accuracy"])
    h2 = model.fit(train_ds, validation_data=val_ds, epochs=args.epochs_finetune,
                   class_weight=class_weight, callbacks=callbacks)

    plot_history([h1, h2], str(out / "training_curves.png"))

    # Final evaluation on data the model has never seen
    y_true = np.concatenate([y.numpy() for _, y in test_pf])
    probs = model.predict(test_pf)
    y_pred = probs.argmax(axis=1)
    report = classification_report(y_true, y_pred, target_names=names, digits=3)
    print("\n" + report)
    (out / "classification_report.txt").write_text(report)
    plot_confusion(confusion_matrix(y_true, y_pred), names, str(out / "confusion_matrix.png"))

    model.save(str(out / "ecorewind_classifier.keras"))
    print(f"\nSaved model and reports to {out}/")


if __name__ == "__main__":
    main()
