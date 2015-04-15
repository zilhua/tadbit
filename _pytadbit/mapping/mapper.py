"""
10 nov. 2014

iterative mapping copied from hiclib

"""

import os
import tempfile
import gzip
import pysam
import gem
from warnings import warn

N_WINDOWS = 0


def get_intersection(fname1, fname2, out_path, verbose=False):
    """
    Merges the two files corresponding to each reads sides. Reads found in both
       files are merged and written in an output file.

    :param fname1: path to a tab separated file generated by the function
       :func:`pytadbit.parsers.sam_parser.parse_sam`
    :param fname2: path to a tab separated file generated by the function
       :func:`pytadbit.parsers.sam_parser.parse_sam`
    :param out_path: path to an outfile. It will written in a similar format as
       the inputs
    """
    reads_fh = open(out_path, 'w')
    reads1 = open(fname1)
    line1 = reads1.next()
    header1 = ''
    while line1.startswith('#'):
        header1 += line1
        line1 = reads1.next()
    read1 = line1.split('\t', 1)[0]

    reads2 = open(fname2)
    line2 = reads2.next()
    header2 = ''
    while line2.startswith('#'):
        header2 += line2
        line2 = reads2.next()
    read2 = line2.split('\t', 1)[0]
    if header1 != header2:
        raise Exception('seems to be mapped onover different chromosomes\n')
    # writes header in output
    reads_fh.write(header1)
    # writes common reads
    count = 0
    try:
        while True:
            if read1 == read2:
                count += 1
                reads_fh.write(line1.strip() + '\t' + 
                               line2.split('\t', 1)[1])
                line1 = reads1.next()
                read1 = line1.split('\t', 1)[0]
                line2 = reads2.next()
                read2 = line2.split('\t', 1)[0]
            elif line1 > line2:
                line2 = reads2.next()
                read2 = line2.split('\t', 1)[0]
            else:
                line1 = reads1.next()
                read1 = line1.split('\t', 1)[0]
    except StopIteration:
        pass
    reads_fh.close()
    if verbose:
        print 'Found %d pair of reads mapping uniquely' % count

def trimming(raw_seq_len, seq_start, min_seq_len):
    return seq_start, raw_seq_len - seq_start - min_seq_len


def iterative_mapping(gem_index_path, fastq_path, out_sam_path,
                      range_start, range_stop, **kwargs):
    """
    :param gem_index_path: path to index file created from a reference genome
       using gem-index tool
    :param fastq_path: 152 bases first 76 from one end, next 76 from the other
       end. Both to be read from left to right.
    :param out_sam_path: path to a directory where to store mapped reads in SAM/
       BAM format (see option output_is_bam).
    :param range_start: list of integers representing the start position of each
       read fragment to be mapped (starting at 1 includes the first nucleotide
       of the read).
    :param range_stop: list of integers representing the end position of each
       read fragment to be mapped.
    :param True single_end: when FASTQ contains paired-ends flags
    :param 4 nthreads: number of threads to use for mapping (number of CPUs)
    :param 0.04 max_edit_distance: The maximum number of edit operations allowed
       while verifying candidate matches by dynamic programming.
    :param 0.04 mismatches: The maximum number of nucleotide substitutions
       allowed while mapping each k-mer. It is always guaranteed that, however
       other options are chosen, all the matches up to the specified number of
       substitutions will be found by the program.
    :param -1 max_reads_per_chunk: maximum number of reads to process at a time.
       If -1, all reads will be processed in one run (more RAM memory needed).
    :param False output_is_bam: Use binary (compressed) form of generated
       out-files with mapped reads (recommended to save disk space).
    :param /tmp temp_dir: important to change. Intermediate FASTQ files will be
       written there.

    :returns: a list of paths to generated outfiles. To be passed to 
       :func:`pytadbit.parsers.sam_parser.parse_sam`
    """
    gem_index_path      = os.path.abspath(os.path.expanduser(gem_index_path))
    fastq_path          = os.path.abspath(os.path.expanduser(fastq_path))
    out_sam_path        = os.path.abspath(os.path.expanduser(out_sam_path))
    single_end          = kwargs.get('single_end'          , True)
    max_edit_distance   = kwargs.get('max_edit_distance'   , 0.04)
    mismatches          = kwargs.get('mismatches'          , 0.04)
    nthreads            = kwargs.get('nthreads'            , 4)
    max_reads_per_chunk = kwargs.get('max_reads_per_chunk' , -1)
    out_files           = kwargs.get('out_files'           , [])
    output_is_bam       = kwargs.get('output_is_bam'       , False)
    temp_dir = os.path.abspath(os.path.expanduser(
        kwargs.get('temp_dir', tempfile.gettempdir())))

    # check kwargs
    for kw in kwargs:
        if not kw in ['single_end', 'nthreads', 'max_edit_distance',
                      'mismatches', 'max_reads_per_chunk',
                      'out_files', 'output_is_bam', 'temp_dir']:
            warn('WARNING: %s not is usual keywords, misspelled?' % kw)
    
    # check windows:
    if (len(zip(range_start, range_stop)) < len(range_start) or
        len(range_start) != len(range_stop)):
        raise Exception('ERROR: range_start and range_stop should have the ' +
                        'same sizes and windows should be uniques.')
    if any([i >= j for i, j in zip(range_start, range_stop)]):
        raise Exception('ERROR: start positions should always be lower than ' +
                        'stop positions.')
    if any([i > 0 for i in range_start]):
        raise Exception('ERROR: start positions should be strictly positive.')
    # create directories
    for rep in [temp_dir, os.path.split(out_sam_path)[0]]:
        try:
            os.mkdir(rep)
        except OSError, error:
            if error.strerror != 'File exists':
                raise error

    #get the length of a read
    if fastq_path.endswith('.gz'):
        fastqh = gzip.open(fastq_path)
    else:
        fastqh = open(fastq_path)
    # get the length from the length of the second line, which is the sequence
    # can not use the "length" keyword, as it is not always present
    try:
        _ = fastqh.next()
        raw_seq_len = len(fastqh.next().strip())
        fastqh.close()
    except StopIteration:
        raise IOError('ERROR: problem reading %s\n' % fastq_path)

    if not  N_WINDOWS:
        N_WINDOWS = len(range_start)
    # Split input files if required and apply iterative mapping to each
    # segment separately.
    if max_reads_per_chunk > 0:
        kwargs['max_reads_per_chunk'] = -1
        print 'Split input file %s into chunks' % fastq_path
        chunked_files = _chunk_file(
            fastq_path,
            os.path.join(temp_dir, os.path.split(fastq_path)[1]),
            max_reads_per_chunk * 4)
        print '%d chunks obtained' % len(chunked_files)
        for i, fastq_chunk_path in enumerate(chunked_files):
            global N_WINDOWS
            N_WINDOWS = 0
            print 'Run iterative_mapping recursively on %s' % fastq_chunk_path
            out_files.extend(iterative_mapping(
                gem_index_path, fastq_chunk_path,
                out_sam_path + '.%d' % (i + 1), range_start[:], range_stop[:],
                **kwargs))

        for i, fastq_chunk_path in enumerate(chunked_files):
            # Delete chunks only if the file was really chunked.
            if len(chunked_files) > 1:
                print 'Remove the chunks: %s' % ' '.join(chunked_files)
                os.remove(fastq_chunk_path)
        return out_files

    # end position according to sequence in the file
    # removes 1 in order to start at 1 instead of 0
    try:
        seq_end = range_stop.pop(0)
        seq_beg = range_start.pop(0)
    except IndexError:
        return out_files

    # define what we trim
    seq_len = seq_end - seq_beg
    trim_5, trim_3 = trimming(raw_seq_len, seq_beg - 1, seq_len - 1)

    # output
    local_out_sam = out_sam_path + '.%d:%d-%d' % (
        N_WINDOWS - len(range_stop), seq_beg, seq_end)
    out_files.append(local_out_sam)
    # input
    inputf = gem.files.open(fastq_path)

    # trimming
    trimmed = gem.filter.run_filter(
        inputf, ['--hard-trim', '%d,%d' % (trim_5, trim_3)],
        threads=nthreads, paired=not single_end)
    
    # mapping
    mapped = gem.mapper(trimmed, gem_index_path, min_decoded_strata=0,
                        max_decoded_matches=2, unique_mapping=False,
                        max_edit_distance=max_edit_distance,
                        mismatches=mismatches,
                        output=temp_dir + '/test.map',
                        threads=nthreads)

    # convert to sam/bam
    if output_is_bam:
        sam = gem.gem2sam(mapped, index=gem_index_path, threads=nthreads,
                          single_end=single_end)
        _ = gem.sam2bam(sam, output=local_out_sam, threads=nthreads)
    else:
        sam = gem.gem2sam(mapped, index=gem_index_path, output=local_out_sam,
                          threads=nthreads, single_end=single_end)

    # Recursively go to the next iteration.
    unmapped_fastq_path = os.path.split(fastq_path)[1]
    if unmapped_fastq_path[-1].isdigit():
        unmapped_fastq_path = unmapped_fastq_path.rsplit('.', 1)[0]
    unmapped_fastq_path = os.path.join(
        temp_dir, unmapped_fastq_path + '.%d:%d-%d' % (
            N_WINDOWS - len(range_stop), seq_beg, seq_end))
    _filter_unmapped_fastq(fastq_path, local_out_sam, unmapped_fastq_path)

    out_files.extend(iterative_mapping(gem_index_path, unmapped_fastq_path,
                                       out_sam_path,
                                       range_start, range_stop, **kwargs))
    os.remove(unmapped_fastq_path)
    return out_files

def _line_count(path):
    '''Count the number of lines in a file. The function was posted by
    Mikola Kharechko on Stackoverflow.
    '''

    f = _gzopen(path)
    lines = 0
    buf_size = 1024 * 1024
    read_f = f.read  # loop optimization

    buf = read_f(buf_size)
    while buf:
        lines += buf.count('\n')
        buf = read_f(buf_size)

    return lines

def _chunk_file(in_path, out_basename, max_num_lines):
    '''Slice lines from a large file.
    The line numbering is as in Python slicing notation.
    '''
    num_lines = _line_count(in_path)
    if num_lines <= max_num_lines:
        return [in_path, ]

    out_paths = []

    for i, line in enumerate(_gzopen(in_path)):
        if i % max_num_lines == 0:
            out_path = out_basename + '.%d' % (i // max_num_lines + 1)
            out_paths.append(out_path)
            out_file = file(out_path, 'w')
        out_file.write(line)

    return out_paths

def _filter_fastq(ids, in_fastq, out_fastq):
    '''Filter FASTQ sequences by their IDs.

    Read entries from **in_fastq** and store in **out_fastq** only those
    the whose ID are in **ids**.
    '''
    out_file = open(out_fastq, 'w')
    in_file = _gzopen(in_fastq)
    while True:
        line = in_file.readline()
        if not line:
            break

        if not line.startswith('@'):
            raise Exception(
                '{0} does not comply with the FASTQ standards.'.format(in_fastq))

        fastq_entry = [line, in_file.readline(),
                       in_file.readline(), in_file.readline()]
        read_id = line.split()[0][1:]
        if read_id.endswith('/1') or read_id.endswith('/2'):
            read_id = read_id[:-2]
        if read_id in ids:
            out_file.writelines(fastq_entry)


def _filter_unmapped_fastq(in_fastq, in_sam, nonunique_fastq):
    '''Read raw sequences from **in_fastq** and alignments from
    **in_sam** and save the non-uniquely aligned and unmapped sequences
    to **unique_sam**.
    '''
    samfile = pysam.Samfile(in_sam)

    nonunique_ids = set()
    for read in samfile:
        tags_dict = dict(read.tags)
        read_id = read.qname
        # If exists, the option 'XS' contains the score of the second
        # best alignment. Therefore, its presence means non-unique alignment.
        if 'XS' in tags_dict or read.is_unmapped or (
            'NH' in tags_dict and int(tags_dict['NH']) > 1):
            nonunique_ids.add(read_id)
            
        # UNMAPPED reads should be included 5% chance to be mapped
        # with larger fragments, so do not do this:
        # if 'XS' in tags_dict or (
        #     'NH' in tags_dict and int(tags_dict['NH']) > 1):
        #     nonunique_ids.add(read_id)

    _filter_fastq(nonunique_ids, in_fastq, nonunique_fastq)

def _gzopen(path):
    if path.endswith('.gz'):
        return gzip.open(path)
    else:
        return open(path)
