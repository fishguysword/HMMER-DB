#!/usr/bin/env python 
# Created by: Lee Bergstrand 
# Descript: A program that extracts the protein annotations from a genbank file and searches these 
#           annotations using HMMsearch and an HMM file. It then stores hits in sqlite database. 
#
# Requirements: - This script requires the Biopython module: http://biopython.org/wiki/Download
#               - This script requires HMMER 3.1 or later.
#               - This script requires sqlLite3 or later.
#
# Usage: HMMExtract.py <organism.gbk> <hmm.hmm> <sqldb.sqlite>
# Example: HMMExtract.py ecoli.gbk helicase.hmm helicasedb.sqlite
#----------------------------------------------------------------------------------------
#===========================================================================================================
#Imports & Setup:
	
import sys
import subprocess
import sqlite3
import csv
import re
from os import path
from Bio import SeqIO 
from multiprocessing import cpu_count

import time # Dev. Import

processors = cpu_count() # Gets number of processor cores for HMMER.

# Regex's
LocusRegex = re.compile("\(Locus:\s\S*\)")
LocationRegex = re.compile("\(Location:\s\[(\S*)\:(\S*)\]\((\S)\)\)")
#===========================================================================================================
# Functions:

# 1: Checks if in proper number of arguments are passed gives instructions on proper use.
def argsCheck(numArgs):
	if len(sys.argv) < numArgs or len(sys.argv) > numArgs:
		print "Sequence Downloader"
		print "By Lee Bergstrand\n"
		print "Usage: " + sys.argv[0] + " <organism.gbk> <hmm.hmm> <sqldb.sqlite>"
		print "Examples: " + sys.argv[0] + " ecoli.gbk helicase.hmm helicasedb.sqlite"
		sys.exit(1) # Aborts program. (exit(1) indicates that an error occurred)
#------------------------------------------------------------------------------------------------------------
# 2: Runs HMMER with settings specific for extracting subject sequences.
def runHMMSearch(FASTA, HMMERDBFile):
	Found16S = True
	process = subprocess.Popen(["hmmsearch", "--acc", "--cpu", str(processors), HMMERDBFile, "-"], stdin = subprocess.PIPE, stdout = subprocess.PIPE, bufsize = 1)
	stdout, error = process.communicate(FASTA) # This returns a list with both stderr and stdout. Only want stdout which is first element.
	if error:
		print error
		sys.exit(1)
	else:
		return stdout
#------------------------------------------------------------------------------------------------------------
# 3: Creates a python dictionary (hash table) that contains the the fasta for each protien in the proteome.
def createProteomeHash(ProteomeFile):
	ProteomeHash = dict() 
	try:
		handle = open(ProteomeFile, "rU")
		proteome = SeqIO.parse(handle, "fasta")
		for record in proteome:
			ProteomeHash.update({ record.id : record.format("fasta") })
		handle.close()
	except IOError:
		print "Failed to open " + ProteomeFile
		exit(1)
		
	return ProteomeHash
#------------------------------------------------------------------------------------------------------------
# 4: Parses HMM searches text output and generates a two dementional array of the domain alignments results.
def parseHmmsearchResults(HMMResults, HMMName, HMMLength):
	HitRowRegex = re.compile("^\s*\d\s*((\?)|(\!))\s*")
	HMMResults = HMMResults.split(">>") # Splits output at domain alignments.
	del HMMResults[0] # Deletes stuff at top of text output which would be the first element after the split.
	HMMResults = [x.split("Alignments")[0] for x in HMMResults] # Removes detailed per alignment info.
	HMMResultsCleaned = []
	for proteinResult in HMMResults:
		proteinResult = proteinResult.splitlines()
		TargetProtein = proteinResult[0].split()[0] # Records protein accession from first line
		for row in proteinResult:
			if HitRowRegex.match(row): # If row is a domain table line.
				row = row.split()
				score   = float(row[2])
				evalue  = float(row[5])
				hmmfrom = int(row[6])
				hmmto   = int(row[7])
				alifrom = int(row[9])
				alito   = int(row[10])
				hmmCoverage = ((float((hmmto - hmmfrom)))/float((HMMLength)))
				DomainRow = [TargetProtein, HMMName, score, evalue, hmmfrom, hmmto, alifrom, alito, hmmCoverage]
				HMMResultsCleaned.append(DomainRow)
	return HMMResultsCleaned
#------------------------------------------------------------------------------------------------------------
# 6: Fitres HMM Hits.
def filterHMMHitTable(HMMHitTable):
	#------------------------------------------------------------------------------------------------------------
	# 6a: This code eliminates hits to the same protien that overlap by greater than 50% by selecting the one with the greater E-value.
	# Note: this code assumes that HMMsearch output domains to the same protein are ordered by hightest to lowest E-value.
	def filterHMMHitTableByOverLap(HMMHitTable):
		FiltredHMMHitTable = list(HMMHitTable)
		for i in range(0,(len(HMMHitTable)-1)):
			RowOne = HMMHitTable[i]    # Current Row in hit table.
			RowTwo = HMMHitTable[i+1]  # Row below the current row.
			if(RowOne[0] == RowTwo[0]): # If they have the same targe protein.
				AlignmentOneLength = RowOne[-2] - RowOne[-3] # RowOne AliTo - AliFrom
				AlignmentTwoLength = RowTwo[-2] - RowTwo[-3] # RowTwo AliTo - AliFrom
				Overlap = RowOne[-2] - RowTwo[-3]  # RowOne AliTo -  RowTwo AliFrom
				
				if (Overlap > 0): # If there is overlap
					# If the overlap is greater than 50% of either alignment.
					if((((float(Overlap)/float(AlignmentOneLength)) > 0.5) or ((float(Overlap)/float(AlignmentTwoLength)) > 0.5))):
						FiltredHMMHitTable.remove(RowOne) # In the normal HMMsearch output domains to the same protein are ordered hightest to
												          # lowest E-value. Therefore only the top row needs to be deleted. 
					
		return FiltredHMMHitTable
	#------------------------------------------------------------------------------------------------------------

	HMMHitTable = [row for row in HMMHitTable if row[3] < float("1e-30")] # Filtres by E-value.
	HMMHitTable = filterHMMHitTableByOverLap(HMMHitTable)
	HMMHitTable = [row for row in HMMHitTable if row[-1] > 0.3] # Filtres by Query Coverage.

	return	HMMHitTable		
#------------------------------------------------------------------------------------------------------------
# 6: Creates list of hits protien FASTAs.
def getHitProteins(HMMHitTable, AnnotationFASTADict, OrganismName):
	HitProteins = []
	for row in HMMHitTable:
		ProteinAccession = row[0]
		ProteinFASTA = AnnotationFASTADict[ProteinAccession]

		Locus = LocusRegex.search(ProteinFASTA).group(0)
		Locus = Locus.split()[1].rstrip(")")

		LocationData = LocationRegex.search(ProteinFASTA)
		Start  = LocationData.group(1)
		End    = LocationData.group(2)
		Strand = LocationData.group(3)
		ProteinData = [ProteinAccession, OrganismName, Locus, Start, End, Strand, ProteinFASTA]
		HitProteins.append(ProteinData)
	return HitProteins
#-----------------------------------------------------------------------------------------------------------
# 7: Inserts organism info into DB.
def insertOrganismInfo(cursor, OrganismInfo):
	try:
		cursor.execute('''INSERT INTO Organisms(Organism_Accession, Organism_Description, Source, Organism_Phylogeny)
			              VALUES(?,?,?,?)''', OrganismInfo)
	except sqlite3.IntegrityError as IntegrityError:
		if "not unique" in str(IntegrityError):
			pass
		else:
			print IntegrityError
			print "The program will be aborted."
			sys.exit(1)
#-----------------------------------------------------------------------------------------------------------
# 8: Inserts organism info into DB.
def insertHits(cursor, HMMHitTable):
	for hit in HMMHitTable:
		try:
			cursor.executemany('''INSERT INTO HMM_Hits(Protein_Accession, HMM_Model, HMM_Score, HMM_E_Value, Ali_From, Ali_To, HMM_From, HMM_To, HMM_Coverage) 
			                      VALUES(?,?,?,?,?,?,?,?,?)''', HMMHitTable)
		except sqlite3.IntegrityError as IntegrityError:
			if "not unique" in str(IntegrityError):
				continue
			else:
				print IntegrityError
				print "The program will be aborted."
				sys.exit(1)	
#-----------------------------------------------------------------------------------------------------------
# 9: Inserts organism info into DB.
def insertProteins(cursor, HitProteins):
	for protein in HitProteins:
		try:
			cursor.execute('''INSERT INTO Proteins(Protein_Accession,Organism_Accession,Locus,Start,End,Strand,FASTA_Sequence)
							  VALUES(?,?,?,?,?,?,?)''', protein)
		except sqlite3.IntegrityError as IntegrityError:
			if "not unique" in str(IntegrityError):
				continue
			else:
				print IntegrityError
				print "The program will be aborted."
				sys.exit(1)	
#===========================================================================================================
# Main program code:
	
# House keeping...
argsCheck(5) # Checks if the number of arguments are correct.

# Stores file one for input checking.
print ">> Starting up..."
OrganismFile = sys.argv[1]
PhyloCSVFile = sys.argv[2]
HMMFile      = sys.argv[3]
sqlFile      = sys.argv[4]

HMMName = path.split(HMMFile)[1].rstrip(".hmm")
OrganismName = path.split(OrganismFile)[1].rstrip(".faa")

# File extension checks
print ">> Performing file extention checks..."
if not OrganismFile.endswith(".faa"):
	print "[Warning] " + OrganismFile + " may not be a fasta file!"
if not PhyloCSVFile.endswith(".csv"):
	print "[Warning] " + PhyloCSVFile + " may not be a csv file!"
if not HMMFile.endswith(".hmm"):
	print "[Warning] " + HMMFile + " may not be a HMM file!"
if not sqlFile.endswith(".sqlite"):
	print "[Warning] " + sqlFile + " may not be a sqlite file!"

# Read in genbank file as a sequence record object.
try:
	print ">> Opening FASTA File: " + OrganismFile
	handle = open(OrganismFile, "rU")
	records = SeqIO.parse(handle, "fasta")
	handle.close()
except IOError:
	print "Failed to open " + OrganismFile
	sys.exit(1)
	
# Opens OrganismDB CSV file for reading and stores as hash table.
OrganismHash = {}
try:
	print ">> Opening CSV File: " + PhyloCSVFile
	readFile = open(PhyloCSVFile, "r")
	reader  = csv.reader(readFile) # Opens file with csv module which takes into account verying csv formats and parses correctly.
	print ">> Good CSV file."
	for row in reader:
		OrganismHash[row[0]] = row
	readFile.close()
except IOError:
	print "Failed to open " + PhyloCSVFile
	sys.exit(1)

# Gets HMM Length.
try:
	HMMLengthRegex = re.compile("^LENG\s*\d*$")
	print ">> Opening HMM File: " + HMMFile
	with open(HMMFile, "rU") as inFile:
		currentLine = inFile.readline()
		while not HMMLengthRegex.match(currentLine):
			currentLine = inFile.readline()
		
		HMMLength = int(currentLine.split()[1])
except IOError:
	print "Failed to open " + HMMFile
	sys.exit(1)
	
print ">> Extracting Protein Annotations..."
AnnotationFASTADict = createProteomeHash(OrganismFile) # Creates a dictionary containing all protein annotations in the gbk file.

print ">> Extracting Organism Info..."
OrganismInfo = OrganismHash[OrganismName]

print ">> Running Hmmsearch..."
FASTAString = "".join(AnnotationFASTADict.values()) # Saves these annotations to a string.
HMMResults  = runHMMSearch(FASTAString, HMMFile) # Runs hmmsearch.

print ">> Parsing and filtering hmmsearch results..."
HMMHitTable = parseHmmsearchResults(HMMResults, HMMName, HMMLength) # Parses hmmsearch results into a two dimensional array.
HMMHitTable = filterHMMHitTable(HMMHitTable)

print ">> Extracting Hit Proteins."
HitProteins = getHitProteins(HMMHitTable, AnnotationFASTADict, OrganismName) # Gets hit protein FASTAs.

if path.isfile(sqlFile):
	try:
		HMMDB = sqlite3.connect(sqlFile)
		print ">> Opened database successfully!";
		cursor = HMMDB.cursor()
		
		insertOrganismInfo(cursor, OrganismInfo)
		cursor.execute('''SELECT * FROM Organisms''')
		print cursor.fetchone()
		
		insertHits(cursor, HMMHitTable)
		cursor.execute('''SELECT * FROM HMM_Hits''')
		for row in cursor.fetchall():
			print row
		
		insertProteins(cursor, HitProteins)	
		cursor.execute('''SELECT * FROM Proteins''')
		for row in cursor.fetchall():
			print row

		HMMDB.commit()
		HMMDB.close()
	except sqlite3.Error as Error:
		print Error
		print "The program will be aborted."
		sys.exit(1)		
else:
	print "Failed to open " + sqlFile
	sys.exit(1)