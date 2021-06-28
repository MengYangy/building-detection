import tensorflow as tf
import cv2 as cv
import os
import glob
import matplotlib.pyplot as plt
import numpy as np
import itertools
from tensorflow.keras import backend as K
from IPython.display import  clear_output
import time
from tensorflow.keras.layers import *
import tensorflow as tf
import cv2 as cv
import numpy as np
from tensorflow.keras.layers import  Conv2D,BatchNormalization,Activation,add,SeparableConv2D,MaxPooling2D


gpus = tf.config.experimental.list_physical_devices('GPU')
if len(gpus) > 0:
    try:
        tf.config.experimental.set_memory_growth(gpus[0], True)
    except RuntimeError:
        pass

train_images_path=glob.glob(r'D:\divide\train\images\*.png')
train_labels_path=glob.glob(r'D:\divide\train\gt\*.png')
assert  len(train_images_path)==len(train_labels_path),'训练集原始图片与标注图片数量不一致，请检查路径是否有误'
print('训练集的样本数:{}'.format(len(train_images_path)))

val_images_path=glob.glob(r'D:\divide\val\images\*.png')
val_labels_path=glob.glob(r'D:\divide\val\labels\*.png')
assert  len(val_images_path)==len(val_labels_path),'验证集原始图片与标注图片数量不一致，请检查路径是否有误'
print('验证集的样本数:{}'.format(len(val_images_path)))


def decode_img(img_path):
    img = cv.imread(img_path)
    img = cv.cvtColor(img, cv.COLOR_BGR2RGB)
    img = cv.resize(img, (512, 512))
    img = np.array(img, np.float32)
    img = img / 127.5 - 1
    #     [512,512,3]
    return img


def decode_lbel(label_path, ):
    label = cv.imread(label_path)
    label = cv.cvtColor(label, cv.COLOR_BGR2GRAY)
    label = cv.resize(label, (512, 512))
    label = label[..., np.newaxis]
    label = np.array(label, np.float32)
    label = label / 255
    #     [512,512,1]
    return label


def train_data_gen(img_path, lab_path, BATCH_SIZE, label_smooth=False, loss="edge_focal_loss"):
    images = img_path
    label = lab_path
    images.sort()
    label.sort()
    zipped = itertools.cycle(zip(images, label))
    batch_size = BATCH_SIZE
    #     先zip后迭代循环
    while True:
        x_train = []
        y_train = []
        for _ in range(batch_size):
            img, seg = next(zipped)

            images = decode_img(img)
            label = decode_lbel(seg)
            # [images_size,images_size,num_classes]
            one_hot_label = tf.keras.utils.to_categorical(label, num_classes=2)

            if label_smooth:
                one_hot_label = (one_hot_label == 1)
                one_hot_label = tf.where(one_hot_label, p_label_smooth, f_label_smooth)
            #                 1--->0.9X,0------>0.0x

            if loss == "edge_focal_loss":
                kernel = np.ones((3, 3), np.uint8)
                label = np.squeeze(label)

                erode = cv.erode(label, kernel, iterations=5)
                #                 建筑物的内部边缘，正样本
                #                 腐蚀 原图-腐蚀

                p_edge = label - erode
                p_edge = (p_edge == 1)
                p_edge = np.where(p_edge, 2.0, 1.0)
                p_edge = p_edge[..., np.newaxis]
                #                 [512,512,1]
                #                 1 or pos_rate

                dilate = cv.dilate(label, kernel, iterations=5)
                f_edge = dilate - label
                f_edge = (f_edge == 1)
                f_edge = np.where(f_edge, 2.0, 1.0)
                f_edge = f_edge[..., np.newaxis]
                #                 [512,512,1]
                #                 1 or neg_rate

                one_hot_label = np.concatenate((one_hot_label, f_edge, p_edge), axis=-1)
            #               [512,512,4]
            x_train.append(images)
            #      [N,512.512.3]
            y_train.append(one_hot_label)
        #      [N,512,512,2]  or [N,512,512,4]
        #         print(x_train.shape)
        yield np.array(x_train), np.array(y_train)


def val_data_gen(img_path, lab_path, BATCH_SIZE, label_smooth=False, loss="edge_focal_loss"):
    images = img_path
    label = lab_path

    images.sort()
    label.sort()
    zipped = itertools.cycle(zip(images, label))
    batch_size = BATCH_SIZE
    while True:
        x_train = []
        y_train = []
        for _ in range(batch_size):
            img, seg = next(zipped)

            images = decode_img(img)
            label = decode_lbel(seg)
            one_hot_label = tf.keras.utils.to_categorical(label, num_classes=2)

            if label_smooth:
                one_hot_label = (one_hot_label == 1)
                one_hot_label = tf.where(one_hot_label, p_label_smooth, f_label_smooth)

            if loss == "edge_focal_loss":
                label = np.squeeze(label)
                kernel = np.ones((3, 3), np.uint8)

                erode = cv.erode(label, kernel, iterations=5)

                p_edge = label - erode
                p_edge = (p_edge == 1)
                p_edge = np.where(p_edge, 2.0, 1.0)
                p_edge = p_edge[..., np.newaxis]
                #               erode==>traget_edge_mask_weights

                dilate = cv.dilate(label, kernel, iterations=5)
                f_edge = dilate - label
                f_edge = (f_edge == 1)
                f_edge = np.where(f_edge, 2.0, 1.0)
                f_edge = f_edge[..., np.newaxis]

                one_hot_label = np.concatenate((one_hot_label, f_edge, p_edge), axis=-1)
            x_train.append(images)
            y_train.append(one_hot_label)
        yield np.array(x_train), np.array(y_train)


def sSE_block(inputs):
    x = Conv2D(1, 1, strides=1, padding='same')(inputs)
    #     B,H,W,C----->B,H,W,
    x = tf.keras.layers.Activation('sigmoid')(x)
    x = tf.multiply(x, inputs)
    return x


def cSE(inputs, rate=16):
    shape = inputs.shape
    #     print(shape)
    x = tf.keras.layers.GlobalAveragePooling2D()(inputs)
    x = Reshape((1, 1, shape[-1]))(x)
    #     print(x.shape)
    x = Conv2D(shape[-1] // 16, 1, strides=1, padding='same')(x)
    x = Conv2D(shape[-1], 1, strides=1, padding='same')(x)
    x = tf.keras.layers.Activation('sigmoid')(x)
    #     B,1,1,C
    x = tf.multiply(x, inputs)
    return x


def scSE_block(inputs):
    s = sSE_block(inputs)
    c = cSE(inputs, rate=16)
    add = tf.add(s, c)
    return add


def UNet(num_classes=2, input_shape=(512, 512, 3)):
    input = Input(shape=input_shape)

    conv1 = Conv2D(64, 3, padding='same', activation='relu')(input)
    conv1 = Conv2D(64, 3, padding='same', activation='relu')(conv1)
    pool1 = MaxPooling2D(pool_size=2)(conv1)

    conv2 = Conv2D(128, 3, padding='same', activation='relu')(pool1)
    conv2 = Conv2D(128, 3, padding='same', activation='relu')(conv2)
    pool2 = MaxPooling2D(pool_size=2)(conv2)

    conv3 = Conv2D(256, 3, padding='same', activation='relu')(pool2)
    conv3 = Conv2D(256, 3, padding='same', activation='relu')(conv3)
    pool3 = MaxPooling2D(pool_size=2)(conv3)

    conv4 = Conv2D(512, 3, padding='same', activation='relu')(pool3)
    conv4 = Conv2D(512, 3, padding='same', activation='relu')(conv4)
    pool4 = MaxPooling2D(pool_size=2)(conv4)

    conv5 = Conv2D(1024, 3, padding='same', activation='relu')(pool4)
    conv5 = Conv2D(1024, 3, padding='same', activation='relu')(conv5)

    up1 = Conv2DTranspose(512, 3, strides=2, padding='same', activation='relu')(conv5)
    concat1 = tf.concat([up1, conv4], axis=-1)
    conv6 = Conv2D(512, 3, padding='same', activation='relu')(concat1)
    conv6 = Conv2D(512, 3, padding='same', activation='relu')(conv6)
    conv6 = scSE_block(conv6)

    up2 = Conv2DTranspose(256, 3, strides=2, padding='same', activation='relu')(conv6)
    concat2 = tf.concat([up2, conv3], axis=-1)
    conv7 = Conv2D(256, 3, padding='same', activation='relu')(concat2)
    conv7 = Conv2D(256, 3, padding='same', activation='relu')(conv7)
    conv7 = scSE_block(conv7)

    up3 = Conv2DTranspose(128, 3, strides=2, padding='same', activation='relu')(conv7)
    concat3 = tf.concat([up3, conv2], axis=-1)
    conv8 = Conv2D(128, 3, padding='same', activation='relu')(concat3)
    conv8 = Conv2D(128, 3, padding='same', activation='relu')(conv8)
    conv8 = scSE_block(conv8)

    up4 = Conv2DTranspose(64, 3, strides=2, padding='same', activation='relu')(conv8)
    concat4 = tf.concat([up4, conv1], axis=-1)
    conv9 = Conv2D(64, 3, padding='same', activation='relu')(concat4)
    conv9 = Conv2D(64, 3, padding='same', activation='relu')(conv9)
    conv9 = scSE_block(conv9)

    outputs = Conv2D(num_classes, 1, padding='same', activation='softmax')(conv9)
    model = tf.keras.Model(inputs=input, outputs=outputs)
    return model


model = UNet(2)
model.summary()


def binary_crossentropy(y_true, y_pred):
    y_true = y_true[:, :, :, :2]
    loss = y_true * tf.math.log(y_pred + K.epsilon())
    #     [N,512,512,2]
    loss_num = 0
    for i in range(2):
        loss_num += loss[:, :, :, i]
    avg_loss = -tf.reduce_mean(loss_num)
    #     sum_and_mean
    return avg_loss


def focal_loss(y_true, y_pred):
    y_true = y_true[:, :, :, :2]
    #     [N,512,512,2]
    loss = [0.5, 0.5] * y_true * (1 - y_pred) * (1 - y_pred) * tf.math.log(y_pred + K.epsilon())
    #     [N,512,512,2]
    #     two_samples
    loss_num = 0
    for i in range(2):
        loss_num += loss[:, :, :, i]
    avg_loss = -tf.reduce_mean(loss_num)
    return avg_loss


def edge_focal_loss(y_true, y_pred):
    y = y_true[:, :, :, :2]
    #     [N,512,512,2]
    edge_mask_weight = y_true[:, :, :, 2:]
    #     [N,512,512,2]
    loss = [0.35, 0.65] * edge_mask_weight * y * (1 - y_pred) * (1 - y_pred) * tf.math.log(y_pred + K.epsilon())
    #     [N,512,512,2]
    #     正负样本的权重与边缘的权重
    loss_num = 0
    for i in range(2):
        loss_num += loss[:, :, :, i]
    avg_loss = -tf.reduce_mean(loss_num)
    return avg_loss


def PA(y_true, y_pred):
    y_true = y_true[:, :, :, :2]
    #     [N,512,512,2]
    y_true = tf.argmax(y_true, axis=-1)
    #     [N,512,512]
    y_true = tf.cast(y_true, tf.int32)

    y_pred = tf.argmax(y_pred, axis=-1)
    #     [N,512,512,2]---->[N,512,512]
    y_pred = tf.cast(y_pred, tf.int32)

    TP = tf.reduce_sum(tf.cast(y_true * y_pred, tf.int32))
    TN = tf.reduce_sum(tf.cast((1 - y_true) * (1 - y_pred), tf.int32))
    FP = tf.reduce_sum(tf.cast((1 - y_true) * y_pred, tf.int32))
    FN = tf.reduce_sum(tf.cast(y_true * (1 - y_pred), tf.int32))
    #     仅针对于二分类的指标评估

    TP = tf.cast(TP, tf.float32)
    TN = tf.cast(TN, tf.float32)
    FP = tf.cast(FP, tf.float32)
    FN = tf.cast(FN, tf.float32)

    PA = (TP + TN) / (TP + TN + FP + FN + K.epsilon())
    return PA


def IoU(y_true, y_pred):
    y_true = y_true[:, :, :, :2]
    y_true = tf.argmax(y_true, axis=-1)
    y_true = tf.cast(y_true, tf.int32)

    y_pred = tf.argmax(y_pred, axis=-1)
    y_pred = tf.cast(y_pred, tf.int32)

    TP = tf.reduce_sum(tf.cast(y_true * y_pred, tf.int32))
    TN = tf.reduce_sum(tf.cast((1 - y_true) * (1 - y_pred), tf.int32))
    FP = tf.reduce_sum(tf.cast((1 - y_true) * y_pred, tf.int32))
    FN = tf.reduce_sum(tf.cast(y_true * (1 - y_pred), tf.int32))

    TP = tf.cast(TP, tf.float32)
    TN = tf.cast(TN, tf.float32)
    FP = tf.cast(FP, tf.float32)
    FN = tf.cast(FN, tf.float32)

    IoU = TP / (TP + FP + FN + K.epsilon())
    return IoU


def MIoU(y_true, y_pred):
    y_true = y_true[:, :, :, :2]
    y_true = tf.argmax(y_true, axis=-1)
    y_true = tf.cast(y_true, tf.int32)

    y_pred = tf.argmax(y_pred, axis=-1)
    y_pred = tf.cast(y_pred, tf.int32)

    TP = tf.reduce_sum(tf.cast(y_true * y_pred, tf.int32))
    TN = tf.reduce_sum(tf.cast((1 - y_true) * (1 - y_pred), tf.int32))
    FP = tf.reduce_sum(tf.cast((1 - y_true) * y_pred, tf.int32))
    FN = tf.reduce_sum(tf.cast(y_true * (1 - y_pred), tf.int32))

    TP = tf.cast(TP, tf.float32)
    TN = tf.cast(TN, tf.float32)
    FP = tf.cast(FP, tf.float32)
    FN = tf.cast(FN, tf.float32)

    IoU0 = TP / (TP + FP + FN + K.epsilon())
    IoU1 = TN / (TN + FP + FN + K.epsilon())
    return (IoU0 + IoU1) / 2


def F1_score(y_true, y_pred):
    y_true = y_true[:, :, :, :2]
    y_true = tf.argmax(y_true, axis=-1)
    y_true = tf.cast(y_true, tf.int32)

    y_pred = tf.argmax(y_pred, axis=-1)
    y_pred = tf.cast(y_pred, tf.int32)

    TP = tf.reduce_sum(tf.cast(y_true * y_pred, tf.int32))
    TN = tf.reduce_sum(tf.cast((1 - y_true) * (1 - y_pred), tf.int32))
    FP = tf.reduce_sum(tf.cast((1 - y_true) * y_pred, tf.int32))
    FN = tf.reduce_sum(tf.cast(y_true * (1 - y_pred), tf.int32))

    TP = tf.cast(TP, tf.float32)
    TN = tf.cast(TN, tf.float32)
    FP = tf.cast(FP, tf.float32)
    FN = tf.cast(FN, tf.float32)

    Recall = TP / (TP + FN + K.epsilon())
    Precision = TP / (TP + FP + K.epsilon())

    F1_score = (2.0 * Precision * Recall) / (Precision + Recall + K.epsilon())
    return F1_score


#   指数下降
def exponential_fall(global_epoch,
                     lr_base=1e-3,
                     decay=0.9,
                     min_lr=0):
    current_lr = lr_base * pow(decay, global_epoch)
    current_lr = max(current_lr, min_lr)
    return current_lr


class ExponentDecayScheduler(tf.keras.callbacks.Callback):
    def __init__(self, learning_rate,
                 decay,
                 global_epoch_init=0,
                 min_learning_rate=0,
                 verbose=0,
                 lr_flag=True):
        super(ExponentDecayScheduler, self).__init__()
        self.learning_rate = learning_rate
        #         初始的学习率
        self.decay = decay
        self.global_epochs = global_epoch_init
        self.min_learning_rate = min_learning_rate
        self.all_lr_num = []
        self.verbose = verbose
        self.print_lr = lr_flag

    # save data,run only

    #     def on_epoch_end(self,logs=None):
    def on_epoch_end(self, epoch, logs=None):
        self.global_epochs += 1
        self.all_lr_num.append(K.get_value(self.model.optimizer.lr))
        if self.print_lr:
            print(
                '\n EPOCH:%d =>lrarning_rate down to:%6f ' % (self.global_epochs, K.get_value(self.model.optimizer.lr)))

    # after training

    def on_epoch_begin(self, epoch, logs=None):
        lr = exponential_fall(global_epoch=self.global_epochs,
                              lr_base=self.learning_rate,
                              decay=self.decay,
                              min_lr=self.min_learning_rate)
        K.set_value(self.model.optimizer.lr, lr)

        if self.verbose > 0:
            print('\nBatch %05d: setting learning '
                  'rate to %s.' % (self.global_epoch + 1, lr))
    # before training


exponent_lr = ExponentDecayScheduler(learning_rate=1e-3,
                                     decay=0.9)


# lr change in all steps
def cosine_decay_with_warmup(global_step,
                             learning_rate_base,
                             total_steps,
                             warmup_learning_rate=0.0,
                             warmup_steps=0,
                             min_learn_rate=0,
                             ):
    # [0,np.pi]======>[1,-1]
    # lr down
    # 0.5*0.0001*（1+cosx），cosx====>[-1,1]
    # 0.0001========>0
    if (global_step > warmup_steps) | (global_step == warmup_steps):
        learning_rate = 0.5 * learning_rate_base * (
                    1 + np.cos(np.pi * (global_step - warmup_steps) / float(total_steps - warmup_steps)))
        return max(learning_rate, min_learn_rate)
    #  lr up
    k = (learning_rate_base - warmup_learning_rate) / warmup_steps
    # lr=kx+b
    learning_rate = k * global_step + warmup_learning_rate
    return max(learning_rate, min_learn_rate)


class WarmUpCosineDecayScheduler(tf.keras.callbacks.Callback):
    def __init__(self,
                 learning_rate_base,
                 total_steps,
                 global_step_init=0,
                 warmup_learning_rate=0.0,
                 warmup_steps=0,
                 min_learn_rate=0,
                 verbose=0):
        super(WarmUpCosineDecayScheduler, self).__init__()
        self.learning_rate_base = learning_rate_base
        self.total_steps = total_steps
        self.global_step = global_step_init
        self.warmup_learning_rate = warmup_learning_rate
        self.warmup_steps = warmup_steps
        self.verbose = verbose
        self.min_learn_rate = min_learn_rate
        self.learning_rates = []

    def on_batch_end(self, batch, logs=None):
        self.global_step = self.global_step + 1
        lr = K.get_value(self.model.optimizer.lr)
        self.learning_rates.append(lr)

    def on_batch_begin(self, batch, logs=None):
        lr = cosine_decay_with_warmup(global_step=self.global_step,
                                      learning_rate_base=self.learning_rate_base,
                                      total_steps=self.total_steps,
                                      warmup_learning_rate=self.warmup_learning_rate,
                                      warmup_steps=self.warmup_steps,
                                      min_learn_rate=self.min_learn_rate)
        K.set_value(self.model.optimizer.lr, lr)
        if self.verbose > 0:
            print('\nBatch %05d: setting learning '
                  'rate to %s.' % (self.global_step + 1, lr))
        # every batch print lr number


BATCH_SIZE = 8
'若提示OOM的错误，代表显存爆炸，需要减小batch_size'
epoch = 30
'网络迭代的次数'
warmup_epoch = 3
one_batch_steps = len(train_images_path) // BATCH_SIZE

warm_up_lr = WarmUpCosineDecayScheduler(learning_rate_base=1e-3,
                                        total_steps=epoch * one_batch_steps,
                                        warmup_learning_rate=1e-5,
                                        #                                         上升起点的lr
                                        warmup_steps=warmup_epoch * one_batch_steps,
                                        min_learn_rate=0
                                        )


# about early stopping
# all_patience_time VS consecutive_all_times
# Monitoring loss VS Monitoring val_loss
class MY_EarlyStoppingAtMinLoss(tf.keras.callbacks.Callback):
    def __init__(self, patience=10):
        super(MY_EarlyStoppingAtMinLoss, self).__init__()
        self.patience = patience * 2
        self.add_time = 0
        self.stopped_epoch = 0
        self.need_stopping = False
        self.all_acc = []

    def on_train_begin(self, logs=None):
        self.best_acc = 0

    def on_epoch_end(self, epoch, logs=None):
        self.stopped_epoch += 1
        current_val_acc = logs.get('val_PA')
        self.all_acc.append(current_val_acc)
        name = "weights1/epoch_{}_weights.h5".format(self.stopped_epoch)
        '训练权重保存的路径'
        self.model.save_weights(name)
        '''
        if (current_val_acc > self.best_acc) | (current_val_acc == self.best_acc):
            self.best_acc = current_val_acc
            self.add_time = 0
            name = "weights1/epoch_{}_weights.h5".format(self.stopped_epoch)
            self.model.save_weights(name)
            print('\n Model weights1 save to:{}'.format(name), end='')

        else:
            self.add_time += 1
            if (self.add_time > self.patience) | (self.add_time == self.patience):
                self.model.stop_training = True
                self.need_stopping = True
        '''

    def on_train_end(self, logs=None):
        if self.need_stopping:
            print("Epoch {}:early stopping".format(self.stopped_epoch))


early_s = MY_EarlyStoppingAtMinLoss(patience=6)


class Display(tf.keras.callbacks.Callback):
    def __init__(self, test_img_path='', test_label_path=''):
        super(Display, self).__init__()
        self.test_img = decode_img(test_img_path)
        self.test_lab = decode_lbel(test_label_path)

    def on_epoch_end(self, epoch, logs=None):
        #         是否清屏
        #         clear_output(wait=True)
        test_img = self.test_img
        test_img = np.expand_dims(test_img, axis=0)
        res = self.model.predict(test_img)
        res = tf.argmax(res, axis=-1)
        res = tf.squeeze(res)
        res = tf.expand_dims(res, axis=-1)

        plt.figure(figsize=(15, 15))
        display_list = [self.test_img, self.test_lab, res]
        title = ['Input Image', 'True Mask', 'Predicted Mask']
        for i in range(len(display_list)):
            plt.subplot(1, len(display_list), i + 1)
            plt.title(title[i])
            plt.imshow(tf.keras.preprocessing.image.array_to_img(display_list[i]))
            plt.axis('off')
        plt.show()


display = Display(test_img_path=val_images_path[0], test_label_path=val_labels_path[0])


model.compile(
    optimizer='adam',
    loss=edge_focal_loss,
    metrics=[PA,IoU,MIoU,F1_score])
#   focal_loss,binary_crossentropy,edge_focal_loss


train_gen=train_data_gen(train_images_path,train_labels_path,BATCH_SIZE)
val_gen=val_data_gen(val_images_path,val_labels_path,BATCH_SIZE)

history=model.fit_generator(generator=train_gen,
                            steps_per_epoch=len(train_images_path)//BATCH_SIZE,
                            epochs=epoch,
                            callbacks=[warm_up_lr,early_s,display],
                            validation_data=val_gen,
                            validation_steps=len(val_images_path)//BATCH_SIZE)






