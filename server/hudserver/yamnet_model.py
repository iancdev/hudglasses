from __future__ import annotations

from dataclasses import dataclass

import tensorflow as tf

import tf_keras


@dataclass(frozen=True, slots=True)
class YamnetParams:
    sample_rate_hz: int = 16000
    stft_window_seconds: float = 0.025
    stft_hop_seconds: float = 0.010
    mel_bins: int = 64
    mel_min_hz: float = 125.0
    mel_max_hz: float = 7500.0
    log_offset: float = 0.001
    patch_window_seconds: float = 0.96
    patch_hop_seconds: float = 0.48
    num_classes: int = 521
    batchnorm_epsilon: float = 1e-4
    batchnorm_momentum: float = 0.99


class _WaveformToLogMelPatches(tf_keras.layers.Layer):
    def __init__(self, params: YamnetParams, **kwargs):
        super().__init__(**kwargs)
        self._p = params

        self._stft_window_length_samples = int(round(self._p.sample_rate_hz * self._p.stft_window_seconds))
        self._stft_hop_length_samples = int(round(self._p.sample_rate_hz * self._p.stft_hop_seconds))

        # YAMNet uses the next power-of-2 FFT length of the window size (for 16kHz/25ms this is 512).
        self._fft_length = 1 << (int(self._stft_window_length_samples) - 1).bit_length()
        self._spectrogram_bins = (self._fft_length // 2) + 1

        self._patch_frames = int(round(self._p.patch_window_seconds / self._p.stft_hop_seconds))
        self._patch_hop_frames = int(round(self._p.patch_hop_seconds / self._p.stft_hop_seconds))

        if self._patch_frames <= 0 or self._patch_hop_frames <= 0:
            raise ValueError("Invalid patch framing parameters")

        self._mel_matrix = None

    def build(self, input_shape):  # noqa: ANN001
        self._mel_matrix = tf.signal.linear_to_mel_weight_matrix(
            num_mel_bins=int(self._p.mel_bins),
            num_spectrogram_bins=int(self._spectrogram_bins),
            sample_rate=int(self._p.sample_rate_hz),
            lower_edge_hertz=float(self._p.mel_min_hz),
            upper_edge_hertz=float(self._p.mel_max_hz),
        )
        super().build(input_shape)

    def call(self, inputs, training=None):  # noqa: ANN001
        del training
        if self._mel_matrix is None:
            raise RuntimeError("Layer not built")

        waveform = tf.convert_to_tensor(inputs, dtype=tf.float32)

        stft = tf.signal.stft(
            signals=waveform,
            frame_length=int(self._stft_window_length_samples),
            frame_step=int(self._stft_hop_length_samples),
            fft_length=int(self._fft_length),
        )
        magnitude_spectrogram = tf.abs(stft)

        mel = tf.matmul(magnitude_spectrogram, tf.cast(self._mel_matrix, magnitude_spectrogram.dtype))

        log_mel = tf.math.log(mel + tf.cast(self._p.log_offset, mel.dtype))

        # Frame into overlapping patches along the time axis (frames).
        patches = tf.signal.frame(
            log_mel,
            frame_length=int(self._patch_frames),
            frame_step=int(self._patch_hop_frames),
            axis=1,
        )
        return patches


def yamnet_model(*, params: YamnetParams | None = None) -> tf_keras.Model:
    """Build YAMNet (waveform -> frame scores) matching `resources/yamnet.h5` weights."""
    p = params or YamnetParams()
    layers = tf_keras.layers

    waveform = layers.Input(shape=(None,), dtype=tf.float32, name="waveform")

    x = layers.Lambda(lambda w: tf.clip_by_value(w, -1.0, 1.0), name="clip_waveform")(waveform)
    patches = _WaveformToLogMelPatches(p, name="features")(x)

    def _reshape_to_nhwc(patches_4d: tf.Tensor) -> tf.Tensor:
        # patches: [B, N, 96, 64] -> [B*N, 96, 64, 1]
        patches_5d = tf.expand_dims(patches_4d, axis=-1)
        return tf.reshape(patches_5d, [-1, int(round(p.patch_window_seconds / p.stft_hop_seconds)), int(p.mel_bins), 1])

    x = layers.Lambda(_reshape_to_nhwc, name="reshape")(patches)

    def _bn(name: str) -> tf_keras.layers.BatchNormalization:
        return layers.BatchNormalization(
            momentum=float(p.batchnorm_momentum),
            epsilon=float(p.batchnorm_epsilon),
            center=True,
            scale=False,
            name=name,
        )

    def _relu6(name: str) -> tf_keras.layers.ReLU:
        return layers.ReLU(name=name)

    x = layers.Conv2D(
        32, (3, 3), strides=(2, 2), padding="same", use_bias=False, name="layer1/conv"
    )(x)
    x = _bn("layer1/conv/bn")(x)
    x = _relu6("layer1/relu")(x)

    # MobileNetV1-style depthwise separable conv blocks (YAMNet).
    # Pointwise filter counts are inferred from the weights file structure.
    block_defs: list[tuple[int, int]] = [
        (1, 64),  # layer2
        (2, 128),  # layer3
        (1, 128),  # layer4
        (2, 256),  # layer5
        (1, 256),  # layer6
        (2, 512),  # layer7
        (1, 512),  # layer8
        (1, 512),  # layer9
        (1, 512),  # layer10
        (1, 512),  # layer11
        (1, 512),  # layer12
        (2, 1024),  # layer13
        (1, 1024),  # layer14
    ]

    for i, (stride, pw_filters) in enumerate(block_defs, start=2):
        prefix = f"layer{i}"
        x = layers.DepthwiseConv2D(
            (3, 3),
            strides=(stride, stride),
            padding="same",
            use_bias=False,
            name=f"{prefix}/depthwise_conv",
        )(x)
        x = _bn(f"{prefix}/depthwise_conv/bn")(x)
        x = _relu6(f"{prefix}/depthwise_conv/relu")(x)
        x = layers.Conv2D(
            int(pw_filters),
            (1, 1),
            strides=(1, 1),
            padding="same",
            use_bias=False,
            name=f"{prefix}/pointwise_conv",
        )(x)
        x = _bn(f"{prefix}/pointwise_conv/bn")(x)
        x = _relu6(f"{prefix}/pointwise_conv/relu")(x)

    x = layers.GlobalAveragePooling2D(name="global_average_pooling2d")(x)
    x = layers.Dense(int(p.num_classes), activation=None, use_bias=True, name="logits")(x)
    frame_pred = layers.Activation("sigmoid", name="prediction")(x)

    return tf_keras.Model(inputs=waveform, outputs=frame_pred, name="yamnet")
