#!/usr/bin/env python
# -*- coding: utf-8 -*-

import argparse
import sys
import clang.cindex
from clang.cindex import CompilationDatabase, CursorKind, Diagnostic, TranslationUnit
import logging
import logging.config
import os
import re
import signal
import threading
from threading import Thread
import time

class DicIDENTIFIER(object):
  def __init__(self):
    self.lock = threading.Lock()
    self.dic = {}

  def setValue(self, key, value):
    self.lock.acquire()
    try:
      self.dic[key] = value
    finally:
      self.lock.release()
    return

  def result_plain(self, use_dic):
    self.lock.acquire()
    try:
      for key in sorted(self.dic.keys()):
        t = self.dic[key]
        if not key in use_dic.dic:
          logging.error(
            '%s, %s (%s line:%s column:%s)' % ('', t.spelling, t.location.file, t.location.line, t.location.column))
        else:
          logging.debug(
            '%s, %s (%s line:%s column:%s)' % ('', t.spelling, t.location.file, t.location.line, t.location.column))
    finally:
      self.lock.release()
    return

  def result_xml(self, filename, use_dic):
    self.lock.acquire()
    try:
      f = open(filename, 'w')
      f.write('<?xml version="1.0" encoding="UTF-8"?>')
      f.write('<results>')
      for key in sorted(self.dic.keys()):
        t = self.dic[key]
        if not key in use_dic.dic:
          f.write('<error file="%s" line="%s" id="unusedDefine" severity="style" msg="Unused define: %s"/>' % 
                  (t.location.file, t.location.line, t.spelling))
        else:
          logging.debug(
            '%s, %s (%s line:%s column:%s)' % ('', t.spelling, t.location.file, t.location.line, t.location.column))
      f.write('</results>')
    finally:
      f.close()
      self.lock.release()
    return

class ParseThread(threading.Thread):

  def __init__(self, group=None, target=None, name=None,
               filename='', def_dic={}, use_dic={}, args=(), parse_arg=None,
               event=None, verbose=None):
    threading.Thread.__init__(self, group=group, target=target, name=name,
                                  verbose=verbose)
    #self.setDaemon(True)
    self.filename = filename
    self.def_dic = def_dic
    self.use_dic = use_dic
    self.args = args
    self.parse_arg = parse_arg
    self.event = event
    return

  def visit_tokens(self, tokens, src_def_dic={}, comment=False):
    do_logout = True
    do_pri = False
    do_pri_if = False
    do_pri_ifdef = False
    do_pri_ifndef = False
    do_pri_def = False
    do_pri_undef = False
    pri_def_key = ""
    for t in tokens:
      do_return = False
      do_comment = comment
      do_subcall = False
      if t.kind.name == 'PUNCTUATION' and t.spelling == '#':
        do_pri = True
      elif t.kind.name == 'IDENTIFIER' and t.spelling == 'undef' and do_pri:
        do_pri_undef = True
        do_pri = False
      elif t.kind.name == 'IDENTIFIER' and do_pri_undef:
        do_pri_undef = False
        del def_dic[t.spelling]
      elif t.kind.name == 'IDENTIFIER' and t.spelling == 'define' and do_pri:
        do_pri_def = True
        do_pri = False
      elif t.kind.name == 'IDENTIFIER' and do_pri_def and pri_def_key == '':
        do_pri = False
        pri_def_key = t.spelling
        if not do_comment:
          src_def_dic[pri_def_key] = t
          self.def_dic.setValue(pri_def_key, t)
      elif t.kind.name == 'IDENTIFIER' and do_pri_def and pri_def_key != '':
        do_pri_def = False
        if not do_comment:
          src_def_dic[pri_def_key] = t.spelling
          logging.debug(
            '%s %s, %s (%s line:%s column:%s)' % ('D', pri_def_key + ' ' + t.kind.name, t.spelling, t.location.file, t.location.line, t.location.column))
        pri_def_key = ''
      elif t.kind.name == 'LITERAL' and do_pri_def:
        do_pri_def = False
        if not do_comment:
          src_def_dic[pri_def_key] = t.spelling
          logging.debug(
            '%s %s, %s (%s line:%s column:%s)' % ('D', pri_def_key + ' ' + t.kind.name, t.spelling, t.location.file, t.location.line, t.location.column))
        pri_def_key = ''
      elif t.kind.name == 'KEYWORD' and t.spelling == 'if' and do_pri:
        do_pri_if = True
        do_pri = False
      elif t.kind.name == 'LITERAL' and do_pri_if:
        do_comment = ('0' == t.spelling)
        do_subcall = True
        do_pri_if = False
      elif t.kind.name == 'IDENTIFIER' and t.spelling == 'ifdef' and do_pri:
        do_pri_ifdef = True
        do_pri = False
      elif t.kind.name == 'IDENTIFIER' and t.spelling == 'ifndef' and do_pri:
        do_pri_ifndef = True
        do_pri = False
      elif t.kind.name == 'IDENTIFIER' and (do_pri_ifdef or do_pri_ifndef):
        if not do_comment:
          if do_pri_ifdef:
            # t.spelling が定義されていないならコメント化
            do_comment = not (t.spelling in src_def_dic)
          elif do_pri_ifndef:
            do_comment = (t.spelling in src_def_dic)
        self.use_dic.setValue(t.spelling, t)
        logging.debug(
          '%s %s, %s (%s line:%s column:%s)' % ('U', t.kind.name, t.spelling, t.location.file, t.location.line, t.location.column))
        do_subcall = True
        do_pri_ifdef = False
      elif t.kind.name == 'KEYWORD' and t.spelling == 'else' and do_pri:
        do_comment = not do_comment
        comment = do_comment
        do_pri = False
      elif t.kind.name == 'IDENTIFIER' and t.spelling == 'endif' and do_pri:
        do_return = True
        do_pri = False
      elif t.kind.name == 'IDENTIFIER' and not do_comment:
        self.use_dic.setValue(t.spelling, t)
        logging.debug(
          '%s %s, %s (%s line:%s column:%s)' % ('U', t.kind.name, t.spelling, t.location.file, t.location.line, t.location.column))
      if self.event.isSet():
        return
      if do_subcall:
        self.visit_tokens(tokens, src_def_dic, do_comment)
      if do_return:
        return
    return

  def run(self):
    logging.info('running with %s %s', self.filename, self.args)
    index = clang.cindex.Index.create()
    src_def_dic = {}
    for key in self.parse_arg.defines:
      src_def_dic[key] = 1
    if self.parse_arg.debug:
      tree = index.parse(self.filename)
      self.visit_tokens(tree.cursor.get_tokens(), src_def_dic, False)
    else:
      try:
        tree = index.parse(self.filename)
        self.visit_tokens(tree.cursor.get_tokens(), src_def_dic, False)
      except Exception as e:
        logging.error("Clang failed to parse '%s':%s" % (" ".join(self.filename), e))
    return

def search_file(parse_arg, threads, event, def_dic, use_dic):
  for path in parse_arg.check_paths:
    logging.info('searching... %s', path)
    files = os.walk(os.path.abspath(path))
    for (root, dirs, files) in os.walk(path):
      for filename in files:
        if len(parse_arg.check_filenames) == 0:
          do_check = True
        else:
          do_check = False
        for key in parse_arg.check_filenames:
          if re.search(key, filename) != None:
            do_check = True
        for key in parse_arg.ignore_files:
          if re.search(key, filename) != None:
            do_check = False
        if do_check:
          t = ParseThread(filename=os.path.join(root,filename), def_dic=def_dic, use_dic=use_dic, args=sys.argv[1:], parse_arg=parse_arg, event=event)
          t.start()
          threads.append(t)
  return

def sighandler(event, signr, handler):
    event.set()

def main():
  parser = argparse.ArgumentParser()

  parser.add_argument('-o', action='store', dest='output_file',
                    default='',
                    help='output file name')
  parser.add_argument('-format', action='store', dest='output_format',
                    default='plain',
                    help='output file format(plain/xml)')
  parser.add_argument('-d', action="store_true", dest='debug',
                    default=False,
                    help='output debug log',
                    )
  parser.add_argument('-D', action='append', dest='defines',
                    default=[],
                    help='add define values to a list')
  parser.add_argument('-p', action='append', dest='check_paths',
                    default=['.'],
                    help='add checking path to a list')
  parser.add_argument('-n', action='append', dest='check_filenames',
                    default=[],
                    help='add checking filename to a list')
  parser.add_argument('-i', action='append', dest='ignore_files',
                    default=[],
                    help='add ignore filename to a list')

  parser.add_argument('--version', action='version', version='%(prog)s 1.0')

  parse_arg = parser.parse_args()
  
  if parse_arg.debug:
    if parse_arg.output_file != '' and parse_arg.output_format == 'plain':
      logging.basicConfig(filename=parse_arg.output_file, filemode='w',
                          level=logging.DEBUG,
                          format='[%(levelname)s] (%(threadName)-10s) %(message)s')
    else:
      logging.basicConfig(level=logging.DEBUG,
                          format='[%(levelname)s] (%(threadName)-10s) %(message)s')
  else:
    if parse_arg.output_file != '' and parse_arg.output_format == 'plain':
      logging.basicConfig(filename=parse_arg.output_file, filemode='w',
                          level=logging.ERROR,
                          format='[%(levelname)s] (%(threadName)-10s) %(message)s')
    else:
      logging.basicConfig(level=logging.ERROR,
                          format='[%(levelname)s] (%(threadName)-10s) %(message)s')

  def_dic = DicIDENTIFIER()
  use_dic = DicIDENTIFIER()
  threads = []
  e = threading.Event()
  signal.signal(signal.SIGINT, (lambda a, b: sighandler(e, a, b)))
  search_file(parse_arg, threads, e, def_dic, use_dic)
  for th in threads:
    while th.isAlive():
      th.join(0.5)
  # 終了待ち
  for th in threads:
    while th.isAlive():
      th.join()
  if parse_arg.output_format == 'xml':
    def_dic.result_xml(parse_arg.output_file, use_dic)
  elif parse_arg.output_format == 'plain':
  	def_dic.result_plain(use_dic)
  return

main()
