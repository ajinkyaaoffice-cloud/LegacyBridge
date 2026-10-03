      *================================================================*
      * FMTPARM - PARAMETER BLOCK FOR CALL 'FMTUTIL'                   *
      *   A = AMOUNT WITH CR   B = AMOUNT WITH FLOATING MINUS          *
      *   D = DATE MM/DD/YYYY  P = RATE AS PERCENT                     *
      *   R = AMOUNT WITH CR, RIGHT JUSTIFIED                          *
      *================================================================*
       01  FMT-PARMS.
           05 FM-STYLE             PIC X.
           05 FM-AMOUNT            PIC S9(9)V99 COMP-3.
           05 FM-DATE              PIC 9(8).
           05 FM-RATE              PIC 9V9(5) COMP-3.
           05 FM-OUT               PIC X(20).
