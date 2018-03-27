"""
Simple U-Net implementation in TensorFlow

Objective: detect vehicles

y = f(X)

X: image (640, 960, 3)
y: mask (640, 960, 1)
   - binary image
   - background is masked 0
   - vehicle is masked 255

Loss function: maximize IOU

    (intersection of prediction & grount truth)
    -------------------------------
    (union of prediction & ground truth)

Notes:
    In the paper, the pixel-wise softmax was used.
    But, I used the IOU because the datasets I used are
    not labeled for segmentations

Original Paper:
    https://arxiv.org/abs/1505.04597
"""
import argparse
import sys
import time
import os
import pandas as pd
import tensorflow as tf

from six.moves import xrange

from nets.unet import Unet


height = 640
width = 960


def _IOU(y_pred, y_true):
    """Returns a (approx) IOU score

    intesection = y_pred.flatten() * y_true.flatten()
    Then, IOU = 2 * intersection / (y_pred.sum() + y_true.sum() + 1e-7) + 1e-7

    Args:
        y_pred (4-D array): (N, H, W, 1)
        y_true (4-D array): (N, H, W, 1)

    Returns:
        float: IOU score
    """
    H, W, _ = y_pred.get_shape().as_list()[1:]

    pred_flat = tf.reshape(y_pred, [-1, H * W])
    true_flat = tf.reshape(y_true, [-1, H * W])

    intersection = 2 * tf.reduce_sum(pred_flat * true_flat, axis=1) + 1e-7
    denominator = tf.reduce_sum(pred_flat, axis=1) + \
                  tf.reduce_sum(true_flat, axis=1) + 1e-7

    return tf.reduce_mean(intersection / denominator)


def make_train_op(y_pred, y_true):
    """Returns a training operation

    Loss function = - IOU(y_pred, y_true)

    IOU is

        (the area of intersection)
        --------------------------
        (the area of two boxes)

    Args:
        y_pred (4-D Tensor): (N, H, W, 1)
        y_true (4-D Tensor): (N, H, W, 1)

    Returns:
        train_op: minimize operation
    """
    loss = -_IOU(y_pred, y_true)

    global_step = tf.train.get_or_create_global_step()

    # other optimizer will be used
    # optim = tf.train.AdamOptimizer()
    optim = tf.train.MomentumOptimizer(0.0001, 0.99)
    return optim.minimize(loss, global_step=global_step)


def get_start_epoch_number(latest_check_point):
    chck = latest_check_point.split('-')
    chck.reverse()
    return int(chck[0]) + 1


def main(_):
    # We want to see all the logging messages for this tutorial.
    tf.logging.set_verbosity(tf.logging.INFO)

    train = pd.read_csv("./data/train.csv")
    n_train = train.shape[0]

    test = pd.read_csv("./data/test.csv")
    n_test = test.shape[0]

    current_time = time.strftime("%m/%d/%H/%M/%S")
    train_logdir = os.path.join(FLAGS.logdir, "train", current_time)
    test_logdir = os.path.join(FLAGS.logdir, "test", current_time)

    tf.reset_default_graph()
    X = tf.placeholder(tf.float32, shape=[None, height, width, 3], name="X")
    y = tf.placeholder(tf.float32, shape=[None, height, width, 1], name="y")
    mode = tf.placeholder(tf.bool, name="mode") # training or not

    pred = Unet(X, mode, FLAGS)

    tf.add_to_collection("inputs", X)
    tf.add_to_collection("inputs", mode)
    tf.add_to_collection("outputs", pred)

    tf.summary.histogram("Predicted Mask", pred)
    tf.summary.image("Predicted Mask", pred)

    update_ops = tf.get_collection(tf.GraphKeys.UPDATE_OPS)

    with tf.control_dependencies(update_ops):
        train_op = make_train_op(pred, y)

    IOU_op = _IOU(pred, y)
    # IOU_op = tf.Print(IOU_op, [IOU_op])
    tf.summary.scalar("IOU", IOU_op)

    train_csv = tf.train.string_input_producer(['./data/train.csv'])
    test_csv = tf.train.string_input_producer(['./data/test.csv'])
    train_image, train_mask = get_image_mask(train_csv)
    test_image, test_mask = get_image_mask(test_csv, augmentation=False)

    X_batch_op, y_batch_op = tf.train.shuffle_batch(
        [train_image, train_mask],
        batch_size=FLAGS.batch_size,
        capacity=FLAGS.batch_size * 5,
        min_after_dequeue=FLAGS.batch_size * 2,
        allow_smaller_final_batch=True)

    X_test_op, y_test_op = tf.train.batch(
        [test_image, test_mask],
        batch_size=FLAGS.batch_size,
        capacity=FLAGS.batch_size * 2,
        allow_smaller_final_batch=True)

    summary_op = tf.summary.merge_all()


    with tf.Session() as sess:
        train_summary_writer = tf.summary.FileWriter(train_logdir, sess.graph)
        test_summary_writer = tf.summary.FileWriter(test_logdir)

        init = tf.global_variables_initializer()
        sess.run(init)

        saver = tf.train.Saver()

        start_epoch = 1
        if os.path.exists(FLAGS.ckpt_dir) and tf.train.checkpoint_exists(FLAGS.ckpt_dir):
            latest_check_point = tf.train.latest_checkpoint(FLAGS.ckpt_dir)
            saver.restore(sess, latest_check_point)
            tf.logging.info('Restore from checkpoint: %s ', latest_check_point)
            start_epoch = get_start_epoch_number(latest_check_point)
        else:
            try:
                os.rmdir(FLAGS.ckpt_dir)
            except FileNotFoundError:
                pass
            os.mkdir(FLAGS.ckpt_dir)

        try:
            tr_batches_per_epoch = int(n_train / FLAGS.batch_size)
            if n_train % FLAGS.batch_size > 0:
                tr_batches_per_epoch += 1
            test_batches_per_epoch = int(n_test / FLAGS.batch_size)
            if n_test % FLAGS.batch_size > 0:
                test_batches_per_epoch += 1

            coord = tf.train.Coordinator()
            threads = tf.train.start_queue_runners(coord=coord)

            tf.logging.info('training start >> ')
            for epoch in xrange(start_epoch, FLAGS.epochs + 1):
                tf.logging.info('epoch #%d start: ', epoch)

                for step in range(tr_batches_per_epoch):
                    X_batch, y_batch = sess.run([X_batch_op, y_batch_op])
                    step_iou, step_summary, _ = sess.run(
                        [IOU_op, summary_op, train_op],
                        feed_dict={X: X_batch,
                                   y: y_batch,
                                   mode: True})

                    train_summary_writer.add_summary(step_summary, step)
                    tf.logging.info('Epoch #%d, step #%d/%d, IOU %f' %
                                    (epoch, step, tr_batches_per_epoch, step_iou))

                tf.logging.info('validation start >> ')
                total_iou = 0
                test_count = 0
                # test_total_step = n_test/FLAGS.batch_size
                for n in range(test_batches_per_epoch):
                    X_test, y_test = sess.run([X_test_op, y_test_op])
                    test_step_iou, test_step_summary = sess.run(
                        [IOU_op, summary_op],
                        feed_dict={X: X_test,
                                   y: y_test,
                                   mode: False})

                    # total_iou += test_step_iou * X_test.shape[0]
                    total_iou += test_step_iou
                    test_count += 1

                    test_summary_writer.add_summary(test_step_summary, epoch)


                total_iou /= test_count
                tf.logging.info('Step #%d/%d, IOU %.1f%%' %
                                    (n, test_batches_per_epoch, total_iou * 100))

                checkpoint_path = os.path.join(FLAGS.ckpt_dir, 'model.ckpt')
                tf.logging.info('Saving to "%s-%d"', checkpoint_path, epoch)
                saver.save(sess, checkpoint_path, global_step=epoch)

        finally:
            coord.request_stop()
            coord.join(threads)
            checkpoint_path = os.path.join(FLAGS.ckpt_dir, 'model.ckpt')
            saver.save(sess, checkpoint_path)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--epochs',
        default=2,
        type=int,
        help="Number of epochs")

    parser.add_argument(
        '--batch-size',
        default=10,
        type=int,
        help="Batch size")

    parser.add_argument(
        '--log_dir',
        type=str,
        default="logdir",
        help="Tensorboard log directory")

    parser.add_argument(
        '--reg',
        type=float,
        default=0.1,
        help="L2 Regularizer Term")

    parser.add_argument(
        '--ckpt_dir',
        type=str,
        default="models",
        help="Checkpoint directory")

    FLAGS, unparsed = parser.parse_known_args()
    tf.app.run(main=main, argv=[sys.argv[0]] + unparsed)
