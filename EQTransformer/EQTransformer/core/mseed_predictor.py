#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sun Jun 21 21:55:54 2020

@author: mostafamousavi

last update: 06/24/2020

"""

from __future__ import print_function
from __future__ import division
from keras import backend as K
from keras.models import load_model
from keras.optimizers import Adam
import tensorflow as tf
import matplotlib
matplotlib.use('agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import math
import csv
import time
from os import listdir
import os
import shutil
from tqdm import tqdm
import argparse
from datetime import datetime, timedelta
import contextlib
import sys
import warnings
from scipy import signal
from matplotlib.lines import Line2D
from obspy import read
from os.path import join
import json
import pickle
import faulthandler; faulthandler.enable()
import obspy
from obspy.signal.trigger import trigger_onset
from .EqT_utils import f1, SeqSelfAttention, FeedForward, LayerNormalization
warnings.filterwarnings("ignore")


try:
    f = open('setup.py')
    for li, l in enumerate(f):
        if li == 8:
            EQT_VERSION = l.split('"')[1]
except Exception:
    EQT_VERSION = None
    

def mseed_predictor(input_dir='downloads_mseeds',
              input_model="sampleData&Model/EqT1D8pre_048.h5",
              stations_json= "station_list.json",
              output_dir="detections",
              detection_threshold=0.5,                
              P_threshold=0.2,
              S_threshold=0.2, 
              number_of_plots=100,
              plot_mode='time',
              loss_weights=[0.05, 0.40, 0.55],
              loss_types=['binary_crossentropy', 'binary_crossentropy', 'binary_crossentropy'],
              normalization_mode='std',
              overlap = 0.3,
              gpuid=None,
              gpu_limit=None): 
 
    """
    To perform a fast detection directly on mseed data.
    This version does not allow uncdertainty estimation or wrinting the probabilities out. 

    Parameters
    ----------
       input_dir: str, (default = None)
           Directory name containing hdf5 and csv files-preprocessed data.
           
       input_model: str, (default = None)
           Path to a trained model.
         
       stations_json: str, (default = None)
           Path to a json file containing station information.
           
       output_dir: str, (default = None)
           Output directory.
           
       detection_threshold: float, (default = 0.20)
           A value which the detection probabilities above it will be considered as an event.  
           
       P_threshold: float, (default = 0.10)
           A value which the P probabilities above it will be considered as P arrival.
           
       S_threshold: float, (default = 0.10)
           A value which the S probabilities above it will be considered as S arrival. 
           
       number_of_plots: {positive integer, None}, (default = 10)
           Number of output plots.     

       plot_mode: string, (default = 'time')
           Plot types, 'time': only time series, 'time_frequency': time and spectrograms. 
           
       loss_weights: list of three floats, (default = [0.05, 0.40, 0.55])
           Loss wieghts for detection, P picking, and S picking respectively.  
           
       loss_types: list of three str, (default = ['binary_crossentropy', 'binary_crossentropy', 'binary_crossentropy'])
           Loss types for detection, P picking, and S picking respectively.
           
       normalization_mode: str, (default = 'std') 
           Mode of normalization for data preprocessing, 'max': maximum amplitude among three componenet, 'std': standard deviation.            
           
      overlap: float, (default = '0.3) 
           If set, detection and picking is performed in overlaping windows. 
           
       gpuid: {positive integer, None}, (default = None) 
           Id of GPU used for the prediction. If using CPU set to None. 
           
       gpu_limit: {positive integer, None}, (default = None) 
           Set the maximum precentage of memomry usage for the GPU. 


    Generates
    -------
    ./output_dir/STATION_OUTPUT/X_prediction_results.csv
        A table containing all the detection, and picking results. Douplicated events are already removed.
        
    ./output_dir/STATION_OUTPUT/X_report.txt
        A summary of parameters used for the prediction and perfomance.
        
    ./output_dir/STATION_OUTPUT/figures 
        A folder containing plots. 
        
    ./time_tracks.pkl
        Contain the time track of the continous data and its type.
        
    """         
    
    parser = argparse.ArgumentParser() 
    parser.add_argument("--input_dir", default= input_dir)    
    parser.add_argument("--input_model", default=input_model) 
    parser.add_argument("--stations_json", default=stations_json)  
    parser.add_argument("--output_dir", default=output_dir)
    parser.add_argument("--detection_threshold", default=detection_threshold)
    parser.add_argument("--P_threshold", default=P_threshold) 
    parser.add_argument("--S_threshold", default=S_threshold) 
    parser.add_argument("--number_of_plots", default=number_of_plots)
    parser.add_argument("--plot_mode", default=plot_mode)
    parser.add_argument("--loss_weights", default=loss_weights)
    parser.add_argument("--loss_types", default=loss_types)
    parser.add_argument("--normalization_mode", default=normalization_mode) 
    parser.add_argument("--overlap", default=overlap)  
    parser.add_argument("--gpuid", default=gpuid)  
    parser.add_argument("--gpu_limit", default=gpu_limit) 
    args = parser.parse_args() 

        
    if args.gpuid:     
        os.environ['CUDA_VISIBLE_DEVICES'] = '{}'.format(args.gpuid)
        tf.Session(config=tf.ConfigProto(log_device_placement=True))
        config = tf.ConfigProto()
        config.gpu_options.allow_growth = True
        config.gpu_options.per_process_gpu_memory_fraction = float(args.gpu_limit) 
        K.tensorflow_backend.set_session(tf.Session(config=config))          
                                  
    class DummyFile(object):
        file = None
        def __init__(self, file):
            self.file = file
    
        def write(self, x):
            # Avoid print() second call (useless \n)
            if len(x.rstrip()) > 0:
                tqdm.write(x, file=self.file)
    
    @contextlib.contextmanager
    def nostdout():
        save_stdout = sys.stdout
        sys.stdout = DummyFile(sys.stdout)
        yield
        sys.stdout = save_stdout
    
 
    print('============================================================================')
    print('Running EqTransformer ', str(EQT_VERSION))
            
    print(' *** Loading the model ...', flush=True)        
    model = load_model(args.input_model, 
                       custom_objects={'SeqSelfAttention': SeqSelfAttention, 
                                       'FeedForward': FeedForward,
                                       'LayerNormalization': LayerNormalization, 
                                       'f1': f1                                                                            
                                        })              
    model.compile(loss = args.loss_types,
                  loss_weights =  args.loss_weights,           
                  optimizer = Adam(lr = 0.001),
                  metrics = [f1])
    print('*** Loading is complete!', flush=True)  


    out_dir = os.path.join(os.getcwd(), str(args.output_dir))
    if os.path.isdir(out_dir):
        print('============================================================================')        
        print(f' *** {out_dir} already exists!')
        inp = input(" --> Type (Yes or y) to create a new empty directory! otherwise it will overwrite!   ")
        if inp.lower() == "yes" or inp.lower() == "y":
            shutil.rmtree(out_dir)  
            os.makedirs(out_dir) 
     
    station_list = [ev.split(".")[0] for ev in listdir(args.input_dir) if ev.split('/')[-1] != '.DS_Store'];
    station_list = sorted(set(station_list))
    
    data_track = dict()

    print(f'######### There are files for {len(station_list)} stations in {args.input_dir} directory. #########', flush=True)
    for ct, st in enumerate(station_list):
    
        save_dir = os.path.join(out_dir, str(st)+'_outputs')
        save_figs = os.path.join(save_dir, 'figures') 
        if os.path.isdir(save_dir):
            shutil.rmtree(save_dir)  
        os.makedirs(save_dir) 
        if args.number_of_plots:
            os.makedirs(save_figs)
            
        plt_n = 0            
        csvPr_gen = open(os.path.join(save_dir,'X_prediction_results.csv'), 'w')          
        predict_writer = csv.writer(csvPr_gen, delimiter=',', quotechar='"', quoting=csv.QUOTE_MINIMAL)
        predict_writer.writerow(['file_name', 
                                 'network',
                                 'station',
                                 'instrument_type',
                                 'station_lat',
                                 'station_lon',
                                 'station_elv',
                                 'event_start_time',
                                 'event_end_time',
                                 'detection_probability',
                                 'detection_uncertainty', 
                                 'p_arrival_time',
                                 'p_probability',
                                 'p_uncertainty',
                                 'p_snr',
                                 's_arrival_time',
                                 's_probability',
                                 's_uncertainty',
                                 's_snr'
                                     ])  
        csvPr_gen.flush()
        print(f'========= Started working on {st}, {ct+1} out of {len(station_list)} ...', flush=True)

        start_Predicting = time.time()       
        
        file_list = [join(st, ev) for ev in listdir(args.input_dir+'/'+st) if ev.split('/')[-1].split('.')[-1].lower() == 'mseed'];   
        mon = [ev.split('__')[1]+'__'+ev.split('__')[2] for ev in file_list ];
        uni_list = list(set(mon))
        uni_list.sort()  
          
        time_slots, comp_types = [], []
        
        # print('============ Station {} has {} chunks of data.'.format(st, len(uni_list)), flush=True)      
        for _, month in enumerate(uni_list):
            print(month)
            matching = [s for s in file_list if month in s]
            npz_data, meta, time_slots, comp_types = _mseed2nparry(args, matching, time_slots, comp_types, st)
            st_time = obspy.core.utcdatetime.UTCDateTime(str(meta['start_time']))
            meta["trace_start_time"] = [str(st_time+(i*60)).replace('T', ' ').replace('Z', '') for i in range(len(npz_data)//6000)]         
            ss = np.vsplit(npz_data, len(npz_data)//6000)
            ss = np.reshape(ss, (len(ss), 6000, 3)) 
        
            predD, predP, predS = model.predict(_normalize(ss, mode = 'std'))
            detection_memory = []
            for ix in range(len(predD)):
                matches, pick_errors, yh3 =  _picker(args, predD[ix][:, 0], predP[ix][:, 0], predS[ix][:, 0])        
                if (len(matches) >= 1) and ((matches[list(matches)[0]][3] or matches[list(matches)[0]][6])):
                    snr = [_get_snr(ss[ix, :, :], matches[list(matches)[0]][3], window = 100), _get_snr(ss[ix, :, :], matches[list(matches)[0]][6], window = 100)]
                    pre_write = len(detection_memory)
                    detection_memory=_output_writter_prediction(meta, predict_writer, csvPr_gen, matches, snr, detection_memory, ix)
                    post_write = len(detection_memory)
                    if plt_n < args.number_of_plots and post_write > pre_write:
                        _plotter_prediction(ss[ix, :, :], args, save_figs, predD[ix][:, 0], predP[ix][:, 0], predS[ix][:, 0], meta["trace_start_time"][ix], matches)
                        plt_n += 1
                            
            if args.overlap:    
                
                npz_data = npz_data[int(6000-(args.overlap*6000)):,:]
                npz_data = npz_data[:(len(npz_data)//6000)*6000,:]  
                st_time = obspy.core.utcdatetime.UTCDateTime(str(meta['start_time']))+int(60-(args.overlap*60))
                meta["trace_start_time"] = [str(st_time+(i*60)).replace('T', ' ').replace('Z', '') for i in range(len(npz_data)//6000)]  
                ss = np.vsplit(npz_data, len(npz_data)//6000)
                ss = np.reshape(ss, (len(ss), 6000, 3)) 
                
                predD, predP, predS = model.predict(_normalize(ss, mode = 'std'))
                for ix in range(len(predD)):
                    matches, pick_errors, yh3 =  _picker(args, predD[ix][:, 0], predP[ix][:, 0], predS[ix][:, 0])        
                    if (len(matches) >= 1) and ((matches[list(matches)[0]][3] or matches[list(matches)[0]][6])):
                        snr = [_get_snr(ss[ix, :, :], matches[list(matches)[0]][3], window = 100), _get_snr(ss[ix, :, :], matches[list(matches)[0]][6], window = 100)] 
                        pre_write = len(detection_memory)
                        detection_memory=_output_writter_prediction(meta, predict_writer, csvPr_gen, matches, snr, detection_memory, ix) 
                        post_write = len(detection_memory)
                        if plt_n < args.number_of_plots and post_write > pre_write:
                            _plotter_prediction(ss[ix, :, :], args, save_figs,predD[ix][:, 0], predP[ix][:, 0], predS[ix][:, 0], meta["trace_start_time"][ix], matches)
                            plt_n += 1   
                                          
        end_Predicting = time.time() 
        data_track[st]=[time_slots, comp_types] 
        delta = (end_Predicting - start_Predicting) 
        hour = int(delta / 3600)
        delta -= hour * 3600
        minute = int(delta / 60)
        delta -= minute * 60
        seconds = delta     
                        
        dd = pd.read_csv(os.path.join(save_dir,'X_prediction_results.csv'))
        print(f'\n', flush=True)
        print(' *** Finished the prediction in: {} hours and {} minutes and {} seconds.'.format(hour, minute, round(seconds, 2)), flush=True)         
        print(' *** Detected: '+str(len(dd))+' events.', flush=True)
        print(' *** Wrote the results into --> " ' + str(save_dir)+' "', flush=True)
        
        with open(os.path.join(save_dir,'X_report.txt'), 'a') as the_file: 
            the_file.write('================== PREDICTION FROM MSEED ===================='+'\n')               
            the_file.write('================== Overal Info =============================='+'\n')               
            the_file.write('date of report: '+str(datetime.now())+'\n')         
            the_file.write('input_model: '+str(args.input_model)+'\n')
            the_file.write('input_dir: '+str(args.input_dir)+'\n')  
            the_file.write('output_dir: '+str(save_dir)+'\n')  
            the_file.write('================== Prediction Parameters ====================='+'\n')  
            the_file.write('finished the prediction in:  {} hours and {} minutes and {} seconds \n'.format(hour, minute, round(seconds, 2))) 
            the_file.write('detected: '+str(len(dd))+' events.'+'\n')                                       
            the_file.write('loss_types: '+str(args.loss_types)+'\n')
            the_file.write('loss_weights: '+str(args.loss_weights)+'\n')
            the_file.write('================== Other Parameters =========================='+'\n')            
            the_file.write('normalization_mode: '+str(args.normalization_mode)+'\n')
            the_file.write('overlap: '+str(args.overlap)+'\n')                  
            the_file.write('detection_threshold: '+str(args.detection_threshold)+'\n')            
            the_file.write('P_threshold: '+str(args.P_threshold)+'\n')
            the_file.write('S_threshold: '+str(args.S_threshold)+'\n')
            the_file.write('number_of_plots: '+str(args.number_of_plots)+'\n')                        
            the_file.write('gpuid: '+str(args.gpuid)+'\n')
            the_file.write('gpu_limit: '+str(args.gpu_limit)+'\n')    


        

    with open('time_tracks.pkl', 'wb') as f:
        pickle.dump(data_track, f, pickle.HIGHEST_PROTOCOL)

       
        
        
def _mseed2nparry(args, matching, time_slots, comp_types, st_name):
    
    json_file = open(args.stations_json)
    stations_ = json.load(json_file)
    
    st = obspy.core.Stream()
    for m in matching:  
        st += read(os.path.join(str(args.input_dir), m))
       
    for tr in st.select(component="Z"):
        time_slots.append((tr.stats.starttime, tr.stats.endtime))
                    
    try:
        st.merge(fill_value=0)                     
    except Exception:
        st =_resampling(st)
        st.merge(fill_value=0) 
    st.detrend('demean')
        
    st.filter(type='bandpass', freqmin = 1.0, freqmax = 45)
    st.taper(max_percentage=0.001, type='cosine', max_length=2) 
    if len([tr for tr in st if tr.stats.sampling_rate != 100.0]) != 0:
        try:
            st.interpolate(100, method="linear")
        except Exception:
            st=_resampling(st)
                    
    st.trim(min([tr.stats.starttime for tr in st]), max([tr.stats.endtime for tr in st]), pad=True, fill_value=0)
        
    chanL = [tr.stats.channel[-1] for tr in st]
    comp_types.append(len(chanL))
        
    padd_size = 6000-((len(st[0].data)-1)%6000)
    npz_data = np.zeros([len(st[0].data)-1+padd_size, 3])
    if 'Z' in chanL:
        npz_data[:len(st[0].data)-1,2] = st[chanL.index('Z')].data[:-1]
    if ('E' in chanL) or ('1' in chanL):
        try: 
            npz_data[:len(st[0].data)-1,0] = st[chanL.index('E')].data[:-1]
        except Exception:
            npz_data[:len(st[0].data)-1,0] = st[chanL.index('1')].data[:-1]
    if ('N' in chanL) or ('2' in chanL):
        try: 
            npz_data[:len(st[0].data)-1,1] = st[chanL.index('N')].data[:-1]
        except Exception:
            npz_data[:len(st[0].data)-1,1] = st[chanL.index('2')].data[:-1]
                
    start_time = st[0].stats.starttime 
                         

    meta = {"start_time":start_time,
            "trace_start_time":None,
            "trace_name":m
             } 
    try:
        meta["receiver_code"]=st[0].stats.station
        meta["instrument_type"]=st[0].stats.channel[:2]
        meta["network_code"]=stations_[st[0].stats.station]['network']
        meta["receiver_latitude"]=stations_[st[0].stats.station]['coords'][0]
        meta["receiver_longitude"]=stations_[st[0].stats.station]['coords'][1]
        meta["receiver_elevation_m"]=stations_[st[0].stats.station]['coords'][2]  
    except Exception:
        meta["receiver_code"]=st_name
        meta["instrument_type"]=stations_[st_name]['channels'][0][:2]
        meta["network_code"]=stations_[st_name]['network']
        meta["receiver_latitude"]=stations_[st_name]['coords'][0]
        meta["receiver_longitude"]=stations_[st_name]['coords'][1]
        meta["receiver_elevation_m"]=stations_[st_name]['coords'][2] 


        
    return npz_data, meta, time_slots, comp_types
          


def _output_writter_prediction(meta, predict_writer, csvPr, matches, snr, detection_memory, idx):
    
    """ 
    Writes the detection & pcking resutls into a csv file.

    Parameters
    ----------
    dataset : hdf5 obj
        Dateset object of the trece.
    predict_writer : obj
        For writing out the detection/picking results in the csv file.        
    csvPr : obj
        For writing out the detection/picking results in the csv file.  
    matches : dic
        Contains the information for the detected and picked event.    
    snr : list of two floats
        Estimated signal to noise ratios for picked P and S phases.       
    detection_memory : list
        Keep the track of detected events.          
        
    Returns
    -------   
    detection_memory : list
        Keep the track of detected events.  
        
    """      

    station_name = meta["receiver_code"]
    station_lat = meta["receiver_latitude"]
    station_lon = meta["receiver_longitude"]
    station_elv = meta["receiver_elevation_m"]
    start_time = meta["trace_start_time"][idx]
    station_name = "{:<4}".format(station_name)
    network_name = meta["network_code"]
    network_name = "{:<2}".format(network_name)
    instrument_type = meta["instrument_type"]
    instrument_type = "{:<2}".format(instrument_type)  

    try:
        start_time = datetime.strptime(start_time, '%Y-%m-%d %H:%M:%S.%f')
    except Exception:
        start_time = datetime.strptime(start_time, '%Y-%m-%d %H:%M:%S')
        
    def _date_convertor(r):  
        if isinstance(r, str):
            mls = r.split('.')
            if len(mls) == 1:
                new_t = datetime.strptime(r, '%Y-%m-%d %H:%M:%S')
            else:
                new_t = datetime.strptime(r, '%Y-%m-%d %H:%M:%S.%f')
        else:
            new_t = r
            
        return new_t
            
    for match, match_value in matches.items():
        ev_strt = start_time+timedelta(seconds= match/100)
        ev_end = start_time+timedelta(seconds= match_value[0]/100)
        
        doublet = [ st for st in detection_memory if abs((st-ev_strt).total_seconds()) < 2]
        
        if len(doublet) == 0: 
            det_prob = round(match_value[1], 2)
                       
            if match_value[3]: 
                p_time = start_time+timedelta(seconds= match_value[3]/100)
            else:
                p_time = None
            p_prob = match_value[4]
            
            if p_prob:
                p_prob = round(p_prob, 2)
                
            if match_value[6]:
                s_time = start_time+timedelta(seconds= match_value[6]/100)
            else:
                s_time = None
            s_prob = match_value[7]               
            if s_prob:
                s_prob = round(s_prob, 2)
                
            predict_writer.writerow([meta["trace_name"], 
                                         network_name,
                                         station_name, 
                                         instrument_type,
                                         station_lat, 
                                         station_lon,
                                         station_elv,
                                         _date_convertor(ev_strt), 
                                         _date_convertor(ev_end), 
                                         det_prob, 
                                         None,                                
                                         _date_convertor(p_time), 
                                         p_prob,
                                         None,
                                         snr[0],
                                         _date_convertor(s_time), 
                                         s_prob,
                                         None, 
                                         snr[1]
                                         ]) 
            
            csvPr.flush()                
            detection_memory.append(ev_strt)                           
            
    return detection_memory
            


def _get_snr(data, pat, window = 200):
    
    """ 
    Estimates SNR.
    
    Parameters
    ----------
    data : numpy array
        3 component data.     
    pat : positive integer
        Sample point where a specific phase arrives.  
    window : positive integer
        The length of window for calculating the SNR (in sample).         
        
    Returns
    -------   
    snr : {float, None}
       Estimated SNR in db.  
        
    """      
       
    snr = None
    if pat:
        try:
            if int(pat) >= window and (int(pat)+window) < len(data):
                nw1 = data[int(pat)-window : int(pat)];
                sw1 = data[int(pat) : int(pat)+window];
                snr = round(10*math.log10((np.percentile(sw1,95)/np.percentile(nw1,95))**2), 1)           
            elif int(pat) < window and (int(pat)+window) < len(data):
                window = int(pat)
                nw1 = data[int(pat)-window : int(pat)];
                sw1 = data[int(pat) : int(pat)+window];
                snr = round(10*math.log10((np.percentile(sw1,95)/np.percentile(nw1,95))**2), 1)
            elif (int(pat)+window) > len(data):
                window = len(data)-int(pat)
                nw1 = data[int(pat)-window : int(pat)];
                sw1 = data[int(pat) : int(pat)+window];
                snr = round(10*math.log10((np.percentile(sw1,95)/np.percentile(nw1,95))**2), 1)    
        except Exception:
            pass
    return snr 

def _detect_peaks(x, mph=None, mpd=1, threshold=0, edge='rising',
                 kpsh=False, valley=False):

    """
    Detect peaks in data based on their amplitude and other features.

    Parameters
    ----------
    x : 1D array_like
        data.
        
    mph : {None, number}, optional (default = None)
        detect peaks that are greater than minimum peak height.
        
    mpd : positive integer, optional (default = 1)
        detect peaks that are at least separated by minimum peak distance (in
        number of data).
        
    threshold : positive number, optional (default = 0)
        detect peaks (valleys) that are greater (smaller) than `threshold`
        in relation to their immediate neighbors.
        
    edge : {None, 'rising', 'falling', 'both'}, optional (default = 'rising')
        for a flat peak, keep only the rising edge ('rising'), only the
        falling edge ('falling'), both edges ('both'), or don't detect a
        flat peak (None).
        
    kpsh : bool, optional (default = False)
        keep peaks with same height even if they are closer than `mpd`.
        
    valley : bool, optional (default = False)
        if True (1), detect valleys (local minima) instead of peaks.

    Returns
    -------
    ind : 1D array_like
        indeces of the peaks in `x`.

    Modified from 
    ----------
    .. [1] http://nbviewer.ipython.org/github/demotu/BMC/blob/master/notebooks/DetectPeaks.ipynb

    """

    x = np.atleast_1d(x).astype('float64')
    if x.size < 3:
        return np.array([], dtype=int)
    if valley:
        x = -x
    # find indices of all peaks
    dx = x[1:] - x[:-1]
    # handle NaN's
    indnan = np.where(np.isnan(x))[0]
    if indnan.size:
        x[indnan] = np.inf
        dx[np.where(np.isnan(dx))[0]] = np.inf
    ine, ire, ife = np.array([[], [], []], dtype=int)
    if not edge:
        ine = np.where((np.hstack((dx, 0)) < 0) & (np.hstack((0, dx)) > 0))[0]
    else:
        if edge.lower() in ['rising', 'both']:
            ire = np.where((np.hstack((dx, 0)) <= 0) & (np.hstack((0, dx)) > 0))[0]
        if edge.lower() in ['falling', 'both']:
            ife = np.where((np.hstack((dx, 0)) < 0) & (np.hstack((0, dx)) >= 0))[0]
    ind = np.unique(np.hstack((ine, ire, ife)))
    # handle NaN's
    if ind.size and indnan.size:
        # NaN's and values close to NaN's cannot be peaks
        ind = ind[np.in1d(ind, np.unique(np.hstack((indnan, indnan-1, indnan+1))), invert=True)]
    # first and last values of x cannot be peaks
    if ind.size and ind[0] == 0:
        ind = ind[1:]
    if ind.size and ind[-1] == x.size-1:
        ind = ind[:-1]
    # remove peaks < minimum peak height
    if ind.size and mph is not None:
        ind = ind[x[ind] >= mph]
    # remove peaks - neighbors < threshold
    if ind.size and threshold > 0:
        dx = np.min(np.vstack([x[ind]-x[ind-1], x[ind]-x[ind+1]]), axis=0)
        ind = np.delete(ind, np.where(dx < threshold)[0])
    # detect small peaks closer than minimum peak distance
    if ind.size and mpd > 1:
        ind = ind[np.argsort(x[ind])][::-1]  # sort ind by peak height
        idel = np.zeros(ind.size, dtype=bool)
        for i in range(ind.size):
            if not idel[i]:
                # keep peaks with the same height if kpsh is True
                idel = idel | (ind >= ind[i] - mpd) & (ind <= ind[i] + mpd) \
                    & (x[ind[i]] > x[ind] if kpsh else True)
                idel[i] = 0  # Keep current peak
        # remove the small peaks and sort back the indices by their occurrence
        ind = np.sort(ind[~idel])

    return ind


def _picker(args, yh1, yh2, yh3):

    """ 
    Performs detection and picking.

    Parameters
    ----------
    args : object
        A argparse object containing all of the input parameters.     
    yh1 : 1D array
        Detection probabilities. 
    yh2 : 1D array
        P arrival probabilities.         
    yh3 : 1D array
        S arrival probabilities.       
   
    Returns
    -------    
    matches : dic
        Contains the information for the detected and picked event.            
        
        matches --> {detection statr-time:[ detection end-time,
                                           detection probability,
                                           detectin uncertainty,
                                           P arrival, 
                                           P probabiliy, 
                                           P uncertainty,
                                           S arrival, 
                                           S probability, 
                                           S uncertainty
                                           
                                           ]}
    pick_errors : dic                
        pick_errors -->  {detection statr-time:[ P_ground_truth - P_pick,   
                                                 S_ground_truth - S_pick,
                                                ]}
    yh3 : 1D array             
        yh3 --> normalized S_probability                              
                
    """               
             
    detection = trigger_onset(yh1, args.detection_threshold, args.detection_threshold)
    pp_arr = _detect_peaks(yh2, mph=args.P_threshold, mpd=1)
    ss_arr = _detect_peaks(yh3, mph=args.S_threshold, mpd=1)
          
    P_PICKS = {}
    S_PICKS = {}
    EVENTS = {}
    matches = {}
    pick_errors = {}
    if len(pp_arr) > 0:
        P_uncertainty = None  
            
        for pick in range(len(pp_arr)): 
            pauto = pp_arr[pick]

            if pauto: 
                P_prob = np.round(yh2[int(pauto)], 3) 
                P_PICKS.update({pauto : [P_prob, P_uncertainty]})                 
                
    if len(ss_arr) > 0:
        S_uncertainty = None  
            
        for pick in range(len(ss_arr)):        
            sauto = ss_arr[pick]
                    
            if sauto: 
                S_prob = np.round(yh3[int(sauto)], 3) 
                S_PICKS.update({sauto : [S_prob, S_uncertainty]})             
            
    if len(detection) > 0:
        D_uncertainty = None  
        
        for ev in range(len(detection)):                                 
                    
            D_prob = np.mean(yh1[detection[ev][0]:detection[ev][1]])
            D_prob = np.round(D_prob, 3)
                    
            EVENTS.update({ detection[ev][0] : [D_prob, D_uncertainty, detection[ev][1]]})            
    
    # matching the detection and picks
    def pair_PS(l1, l2, dist):
        l1.sort()
        l2.sort()
        b = 0
        e = 0
        ans = []
        
        for a in l1:
            while l2[b] and b < len(l2) and a - l2[b] > dist:
                b += 1
            while l2[e] and e < len(l2) and l2[e] - a <= dist:
                e += 1
            ans.extend([[a,x] for x in l2[b:e]])
            
        best_pair = None
        for pr in ans: 
            ds = pr[1]-pr[0]
            if abs(ds) < dist:
                best_pair = pr
                dist = ds           
        return best_pair


    for ev in EVENTS:
        bg = ev
        ed = EVENTS[ev][2]
        S_error = None
        P_error = None        
        if int(ed-bg) >= 10:
                                    
            candidate_Ss = {}
            for Ss, S_val in S_PICKS.items():
                if Ss > bg and Ss < ed:
                    candidate_Ss.update({Ss : S_val}) 
             
            if len(candidate_Ss) > 1:                            
                candidate_Ss = {list(candidate_Ss.keys())[0] : candidate_Ss[list(candidate_Ss.keys())[0]]}


            if len(candidate_Ss) == 0:
                    candidate_Ss = {None:[None, None]}

            candidate_Ps = {}
            for Ps, P_val in P_PICKS.items():
                if list(candidate_Ss)[0]:
                    if Ps > bg-100 and Ps < list(candidate_Ss)[0]-10:
                        candidate_Ps.update({Ps : P_val}) 
                else:         
                    if Ps > bg-100 and Ps < ed:
                        candidate_Ps.update({Ps : P_val}) 
                    
            if len(candidate_Ps) > 1:
                Pr_st = 0
                buffer = {}
                for PsCan, P_valCan in candidate_Ps.items():
                    if P_valCan[0] > Pr_st:
                        buffer = {PsCan : P_valCan} 
                        Pr_st = P_valCan[0]
                candidate_Ps = buffer
                    
            if len(candidate_Ps) == 0:
                    candidate_Ps = {None:[None, None]}

            if list(candidate_Ss)[0] or list(candidate_Ps)[0]:                 
                matches.update({
                                bg:[ed, 
                                    EVENTS[ev][0], 
                                    EVENTS[ev][1], 
                                
                                    list(candidate_Ps)[0],  
                                    candidate_Ps[list(candidate_Ps)[0]][0], 
                                    candidate_Ps[list(candidate_Ps)[0]][1],  
                                                
                                    list(candidate_Ss)[0],  
                                    candidate_Ss[list(candidate_Ss)[0]][0], 
                                    candidate_Ss[list(candidate_Ss)[0]][1],  
                                                ] })

                                          
                pick_errors.update({bg:[P_error, S_error]})
      
    return matches, pick_errors, yh3


def _resampling(st):
    need_resampling = [tr for tr in st if tr.stats.sampling_rate != 100.0]
    if len(need_resampling) > 0:
       # print('resampling ...', flush=True)    
        for indx, tr in enumerate(need_resampling):
            if tr.stats.delta < 0.01:
                tr.filter('lowpass',freq=45,zerophase=True)
            tr.resample(100)
            tr.stats.sampling_rate = 100
            tr.stats.delta = 0.01
            tr.data.dtype = 'int32'
            st.remove(tr)                    
            st.append(tr) 
    return st 

def _normalize(data, mode = 'max'):          
    data -= np.mean(data, axis=0, keepdims=True)
    if mode == 'max':
        max_data = np.max(data, axis=0, keepdims=True)
        assert(max_data.shape[-1] == data.shape[-1])
        max_data[max_data == 0] = 1
        data /= max_data              

    elif mode == 'std':               
        std_data = np.std(data, axis=0, keepdims=True)
        assert(std_data.shape[-1] == data.shape[-1])
        std_data[std_data == 0] = 1
        data /= std_data
    return data
    





def _plotter_prediction(data, args, save_figs, yh1, yh2, yh3, evi, matches):

    """ 
    Generates plots.

    Parameters
    ----------
    data : numpy array
        3 component raw waveform.
    evi : str
        Trace name.  
    args : object
        A argparse object containing all of the input parameters. 
    save_figs : str
        Path to the folder for saving the plots. 
    yh1 : 1D array
        Detection probabilities. 
    yh2 : 1D array
        P arrival probabilities.         
    yh3 : 1D array
        S arrival probabilities.  
    matches : dic
        Contains the information for the detected and picked event.            
        
    """  

    font0 = {'family': 'serif',
            'color': 'white',
            'stretch': 'condensed',
            'weight': 'normal',
            'size': 12,
            } 
   
    spt, sst, detected_events = [], [], []
    for match, match_value in matches.items():
        detected_events.append([match, match_value[0]])
        if match_value[3]: 
            spt.append(match_value[3])
        else:
            spt.append(None)
            
        if match_value[6]:
            sst.append(match_value[6])
        else:
            sst.append(None)    
            
    if args.plot_mode == 'time_frequency':
    
        fig = plt.figure(constrained_layout=False)
        widths = [6, 1]
        heights = [1, 1, 1, 1, 1, 1, 1.8]
        spec5 = fig.add_gridspec(ncols=2, nrows=7, width_ratios=widths,
                              height_ratios=heights, left=0.1, right=0.9, hspace=0.1)
        
        
        ax = fig.add_subplot(spec5[0, 0])         
        plt.plot(data[:, 0], 'k')
        plt.xlim(0, 6000)
        x = np.arange(6000)
        plt.title(save_figs.split('/')[-2].split('_')[0]+':'+str(evi))
                     
        ax.set_xticks([])
        plt.rcParams["figure.figsize"] = (10, 10)
        legend_properties = {'weight':'bold'} 
        
        pl = None
        sl = None            
        
        if len(spt) > 0 and np.count_nonzero(data[:, 0]) > 10:
            ymin, ymax = ax.get_ylim()
            for ipt, pt in enumerate(spt):
                if pt and ipt == 0:
                    pl = plt.vlines(int(pt), ymin, ymax, color='c', linewidth=2, label='Picked P')
                elif pt and ipt > 0:
                    pl = plt.vlines(int(pt), ymin, ymax, color='c', linewidth=2)
                    
        if len(sst) > 0 and np.count_nonzero(data[:, 0]) > 10: 
            for ist, st in enumerate(sst): 
                if st and ist == 0:
                    sl = plt.vlines(int(st), ymin, ymax, color='m', linewidth=2, label='Picked S')
                elif st and ist > 0:
                    sl = plt.vlines(int(st), ymin, ymax, color='m', linewidth=2)
        
    
        ax = fig.add_subplot(spec5[0, 1])                 
        if pl or sl: 
            custom_lines = [Line2D([0], [0], color='k', lw=0),
                            Line2D([0], [0], color='c', lw=2),
                            Line2D([0], [0], color='m', lw=2)]
            plt.legend(custom_lines, ['E', 'Picked P', 'Picked S'], fancybox=True, shadow=True)
            plt.axis('off')
        
        ax = fig.add_subplot(spec5[1, 0])         
        f, t, Pxx = signal.stft(data[:, 0], fs=100, nperseg=80)
        Pxx = np.abs(Pxx)                       
        plt.pcolormesh(t, f, Pxx, alpha=None, cmap='hot', shading='flat', antialiased=True)
        plt.ylim(0, 40)
        plt.text(1, 1, 'STFT', fontdict=font0)
        plt.ylabel('Hz', fontsize=12)
        ax.set_xticks([])
             
        ax = fig.add_subplot(spec5[2, 0])   
        plt.plot(data[:, 1] , 'k')
        plt.xlim(0, 6000)  
            
        ax.set_xticks([])
        if len(spt) > 0 and np.count_nonzero(data[:, 1]) > 10:
            ymin, ymax = ax.get_ylim()
            for ipt, pt in enumerate(spt):
                if pt and ipt == 0:
                    pl = plt.vlines(int(pt), ymin, ymax, color='c', linewidth=2, label='Picked P')
                elif pt and ipt > 0:
                    pl = plt.vlines(int(pt), ymin, ymax, color='c', linewidth=2) 
                    
        if len(sst) > 0 and np.count_nonzero(data[:, 1]) > 10: 
            for ist, st in enumerate(sst): 
                if st and ist == 0:
                    sl = plt.vlines(int(st), ymin, ymax, color='m', linewidth=2, label='Picked S')
                elif st and ist > 0:
                    sl = plt.vlines(int(st), ymin, ymax, color='m', linewidth=2)
                    
        ax = fig.add_subplot(spec5[2, 1])         
        if pl or sl:
            custom_lines = [Line2D([0], [0], color='k', lw=0),
                            Line2D([0], [0], color='c', lw=2),
                            Line2D([0], [0], color='m', lw=2)]
            plt.legend(custom_lines, ['N', 'Picked P', 'Picked S'], fancybox=True, shadow=True)
            plt.axis('off')
    
    
        ax = fig.add_subplot(spec5[3, 0]) 
        f, t, Pxx = signal.stft(data[:, 1], fs=100, nperseg=80)
        Pxx = np.abs(Pxx)                       
        plt.pcolormesh(t, f, Pxx, alpha=None, cmap='hot', shading='flat', antialiased=True)
        plt.ylim(0, 40)
        plt.text(1, 1, 'STFT', fontdict=font0)
        plt.ylabel('Hz', fontsize=12)
        ax.set_xticks([])        
                       
        
        ax = fig.add_subplot(spec5[4, 0]) 
        plt.plot(data[:, 2], 'k') 
        plt.xlim(0, 6000)   
            
        ax.set_xticks([])               
        if len(spt) > 0 and np.count_nonzero(data[:, 2]) > 10:
            ymin, ymax = ax.get_ylim()
            for ipt, pt in enumerate(spt):
                if pt and ipt == 0:
                    pl = plt.vlines(int(pt), ymin, ymax, color='c', linewidth=2, label='Picked P')
                elif pt and ipt > 0:
                    pl = plt.vlines(int(pt), ymin, ymax, color='c', linewidth=2) 
                    
        if len(sst) > 0 and np.count_nonzero(data[:, 2]) > 10:
            for ist, st in enumerate(sst): 
                if st and ist == 0:
                    sl = plt.vlines(int(st), ymin, ymax, color='m', linewidth=2, label='Picked S')
                elif st and ist > 0:
                    sl = plt.vlines(int(st), ymin, ymax, color='m', linewidth=2)  
                    
        ax = fig.add_subplot(spec5[4, 1])                         
        if pl or sl:    
            custom_lines = [Line2D([0], [0], color='k', lw=0),
                            Line2D([0], [0], color='c', lw=2),
                            Line2D([0], [0], color='m', lw=2)]
            plt.legend(custom_lines, ['Z', 'Picked P', 'Picked S'], fancybox=True, shadow=True)
            plt.axis('off')        
    
        ax = fig.add_subplot(spec5[5, 0])         
        f, t, Pxx = signal.stft(data[:, 2], fs=100, nperseg=80)
        Pxx = np.abs(Pxx)                       
        plt.pcolormesh(t, f, Pxx, alpha=None, cmap='hot', shading='flat', antialiased=True)
        plt.ylim(0, 40)
        plt.text(1, 1, 'STFT', fontdict=font0)
        plt.ylabel('Hz', fontsize=12)
        ax.set_xticks([])                   
            
        ax = fig.add_subplot(spec5[6, 0])
        x = np.linspace(0, data.shape[0], data.shape[0], endpoint=True)
                               
        plt.plot(x, yh1, '--', color='g', alpha = 0.5, linewidth=2, label='Earthquake')
        plt.plot(x, yh2, '--', color='b', alpha = 0.5, linewidth=2, label='P_arrival')
        plt.plot(x, yh3, '--', color='r', alpha = 0.5, linewidth=2, label='S_arrival')
        plt.tight_layout()       
        plt.ylim((-0.1, 1.1)) 
        plt.xlim(0, 6000)
        plt.ylabel('Probability', fontsize=12) 
        plt.xlabel('Sample', fontsize=12) 
        plt.yticks(np.arange(0, 1.1, step=0.2))
        axes = plt.gca()
        axes.yaxis.grid(color='lightgray')        
    
        ax = fig.add_subplot(spec5[6, 1])  
        custom_lines = [Line2D([0], [0], linestyle='--', color='mediumblue', lw=2),
                        Line2D([0], [0], linestyle='--', color='c', lw=2),
                        Line2D([0], [0], linestyle='--', color='m', lw=2)]
        plt.legend(custom_lines, ['Earthquake', 'P_arrival', 'S_arrival'], fancybox=True, shadow=True)
        plt.axis('off')
            
        font = {'family': 'serif',
                    'color': 'dimgrey',
                    'style': 'italic',
                    'stretch': 'condensed',
                    'weight': 'normal',
                    'size': 12,
                    }
        
        plt.text(1, 0.2, 'EQTransformer', fontdict=font)
        if EQT_VERSION:
            plt.text(2000, 0.05, str(EQT_VERSION), fontdict=font)
            
        plt.xlim(0, 6000)
        fig.tight_layout()
        fig.savefig(os.path.join(save_figs, str(evi)+'.png')) 
        plt.close(fig)
        plt.clf()
    

    else:        
        
        ########################################## ploting only in time domain
        fig = plt.figure(constrained_layout=True)
        widths = [1]
        heights = [1.6, 1.6, 1.6, 2.5]
        spec5 = fig.add_gridspec(ncols=1, nrows=4, width_ratios=widths,
                              height_ratios=heights)
        
        ax = fig.add_subplot(spec5[0, 0])         
        plt.plot(data[:, 0], 'k')
        x = np.arange(6000)
        plt.xlim(0, 6000)            
        plt.title(save_figs.split('/')[-2].split('_')[0]+':'+str(evi))

        plt.ylabel('Amplitude\nCounts')
                                          
        plt.rcParams["figure.figsize"] = (8,6)
        legend_properties = {'weight':'bold'}  
        
        pl = sl = None        
        if len(spt) > 0 and np.count_nonzero(data[:, 0]) > 10:
            ymin, ymax = ax.get_ylim()
            for ipt, pt in enumerate(spt):
                if pt and ipt == 0:
                    pl = plt.vlines(int(pt), ymin, ymax, color='c', linewidth=2, label='Picked P')
                elif pt and ipt > 0:
                    pl = plt.vlines(int(pt), ymin, ymax, color='c', linewidth=2)
                    
        if len(sst) > 0 and np.count_nonzero(data[:, 0]) > 10: 
            for ist, st in enumerate(sst): 
                if st and ist == 0:
                    sl = plt.vlines(int(st), ymin, ymax, color='m', linewidth=2, label='Picked S')
                elif st and ist > 0:
                    sl = plt.vlines(int(st), ymin, ymax, color='m', linewidth=2)
                    
        if pl or sl:    
            box = ax.get_position()
            ax.set_position([box.x0, box.y0, box.width * 0.8, box.height])
            custom_lines = [Line2D([0], [0], color='k', lw=0),
                            Line2D([0], [0], color='c', lw=2),
                            Line2D([0], [0], color='m', lw=2)]
            plt.legend(custom_lines, ['E', 'Picked P', 'Picked S'], 
                       loc='center left', bbox_to_anchor=(1, 0.5), 
                       fancybox=True, shadow=True)
                                           
        ax = fig.add_subplot(spec5[1, 0])   
        plt.plot(data[:, 1] , 'k')
        plt.xlim(0, 6000)            
        plt.ylabel('Amplitude\nCounts')            
                  
        if len(spt) > 0 and np.count_nonzero(data[:, 1]) > 10:
            ymin, ymax = ax.get_ylim()
            for ipt, pt in enumerate(spt):
                if pt and ipt == 0:
                    pl = plt.vlines(int(pt), ymin, ymax, color='c', linewidth=2, label='Picked P')
                elif pt and ipt > 0:
                    pl = plt.vlines(int(pt), ymin, ymax, color='c', linewidth=2)
                    
        if len(sst) > 0 and np.count_nonzero(data[:, 1]) > 10: 
            for ist, st in enumerate(sst): 
                if st and ist == 0:
                    sl = plt.vlines(int(st), ymin, ymax, color='m', linewidth=2, label='Picked S')
                elif st and ist > 0:
                    sl = plt.vlines(int(st), ymin, ymax, color='m', linewidth=2)
    
        if pl or sl:
            box = ax.get_position()
            ax.set_position([box.x0, box.y0, box.width * 0.8, box.height])
            custom_lines = [Line2D([0], [0], color='k', lw=0),
                            Line2D([0], [0], color='c', lw=2),
                            Line2D([0], [0], color='m', lw=2)]
            plt.legend(custom_lines, ['N', 'Picked P', 'Picked S'], 
                       loc='center left', bbox_to_anchor=(1, 0.5), 
                       fancybox=True, shadow=True)
                         
        ax = fig.add_subplot(spec5[2, 0]) 
        plt.plot(data[:, 2], 'k') 
        plt.xlim(0, 6000)                    
        plt.ylabel('Amplitude\nCounts')
            
        ax.set_xticks([])
                   
        if len(spt) > 0 and np.count_nonzero(data[:, 2]) > 10:
            ymin, ymax = ax.get_ylim()
            for ipt, pt in enumerate(spt):
                if pt and ipt == 0:
                    pl = plt.vlines(int(pt), ymin, ymax, color='c', linewidth=2, label='Picked P')
                elif pt and ipt > 0:
                    pl = plt.vlines(int(pt), ymin, ymax, color='c', linewidth=2)
                    
        if len(sst) > 0 and np.count_nonzero(data[:, 2]) > 10:
            for ist, st in enumerate(sst): 
                if st and ist == 0:
                    sl = plt.vlines(int(st), ymin, ymax, color='m', linewidth=2, label='Picked S')
                elif st and ist > 0:
                    sl = plt.vlines(int(st), ymin, ymax, color='m', linewidth=2)
                    
        if pl or sl:    
            box = ax.get_position()
            ax.set_position([box.x0, box.y0, box.width * 0.8, box.height])
            custom_lines = [Line2D([0], [0], color='k', lw=0),
                            Line2D([0], [0], color='c', lw=2),
                            Line2D([0], [0], color='m', lw=2)]
            plt.legend(custom_lines, ['Z', 'Picked P', 'Picked S'], 
                       loc='center left', bbox_to_anchor=(1, 0.5), 
                       fancybox=True, shadow=True)       
                   
        ax = fig.add_subplot(spec5[3, 0])
        x = np.linspace(0, data.shape[0], data.shape[0], endpoint=True)
                            
        plt.plot(x, yh1, '--', color='g', alpha = 0.5, linewidth=1.5, label='Earthquake')
        plt.plot(x, yh2, '--', color='b', alpha = 0.5, linewidth=1.5, label='P_arrival')
        plt.plot(x, yh3, '--', color='r', alpha = 0.5, linewidth=1.5, label='S_arrival')
            
        plt.tight_layout()       
        plt.ylim((-0.1, 1.1)) 
        plt.xlim(0, 6000)                                            
        plt.ylabel('Probability') 
        plt.xlabel('Sample')  
        plt.legend(loc='lower center', bbox_to_anchor=(0., 1.17, 1., .102), ncol=3, mode="expand",
                       prop=legend_properties,  borderaxespad=0., fancybox=True, shadow=True)
        plt.yticks(np.arange(0, 1.1, step=0.2))
        axes = plt.gca()
        axes.yaxis.grid(color='lightgray')
            
        font = {'family': 'serif',
                    'color': 'dimgrey',
                    'style': 'italic',
                    'stretch': 'condensed',
                    'weight': 'normal',
                    'size': 12,
                    }
    
        plt.text(6500, 0.5, 'EQTransformer', fontdict=font)
        if EQT_VERSION:
            plt.text(7000, 0.1, str(EQT_VERSION), fontdict=font)
            
        fig.tight_layout()
        fig.savefig(os.path.join(save_figs, str(evi)+'.png')) 
        plt.close(fig)
        plt.clf()
        